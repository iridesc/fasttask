"""MCP 适配层：把 FastTask 既有的 HTTP 接口翻译成 MCP 工具。

设计原则
--------
- **纯翻译层**：不引入任何新的状态或机制。任务队列、状态存储、并发控制、
  超时与撤销仍然全部由 FastTask 自己负责，MCP 只做协议与语义的转换。
- **工具集合跟随 API_* 开关**：``API_CREATE`` / ``API_CHECK`` / ``API_RUN``
  决定注册哪些工具，工具描述也随之调整，保证“描述里提到的能力”一定可用。
- **返回值保持轻量**：``check_*`` 只回状态与结果引用。结果可能到几十 MB，
  外置存储时返回预签名下载地址，由调用方下载到本地再解析，
  避免把整个结果灌进模型上下文。
- **无状态传输**：``stateless_http=True``。``UVICORN_WORKERS`` 默认为 2，
  有状态模式会让会话散落在不同 worker 上而随机返回 404 Session not found。
"""

import asyncio
import base64
import datetime
import inspect
import json
import secrets
import traceback
import uuid
from importlib import import_module
from typing import Annotated

from celery_app import app as celery_app

from utils.api_utils import (
    TaskState,
    get_pending_task_count,
    get_task_statistics_info,
    get_worker_status,
    load_redis_task_infos,
    load_user_to_passwd,
)
from utils.result_storage import (
    RESULT_TYPE_JSON,
    RESULT_TYPE_S3,
    RESULT_TYPE_TEXT,
    build_s3_result_response,
    detect_stored_result_type,
)
from utils.tools import get_bool_env


MCP_PATH = "/mcp"
MCP_SERVER_NAME = "fasttask"

# run_* 是同步执行、结果直接进上下文，超过该体量就截断并引导改用 create + check
_RUN_INLINE_LIMIT = 200 * 1024

_MCP_INSTRUCTIONS = """FastTask 异步任务平台。

每个任务会提供以下工具（取决于服务端开启的接口）：
- create_<task>：创建异步任务，立即返回 result_id
- check_<task>：查询任务状态与结果，用 result_id 查询
- run_<task>：同步执行，仅适合秒级完成的任务

典型流程：create_<task> 拿到 result_id → check_<task> 轮询 → 取回结果。

重要：任务结果可能很大。当 check 返回的 result_type 为 "s3" 时，
result.url 是可直接下载的预签名地址，请先用 shell 下载到本地、
再用 jq 等工具按需提取字段，不要直接把整个结果读入上下文：

    curl -s -o result.json "<result.url>"
    jq '.some_field' result.json
"""


# --------------------------------------------------------------------------- #
# 工具实现（语义与 HTTP 接口一一对应）
# --------------------------------------------------------------------------- #
def _load_loaded_task(task_name):
    """取运行期生成的 Celery 任务对象（loaded_tasks._<task_name>）。"""
    module = import_module(package="loaded_tasks", name=f".{task_name}")
    return getattr(module, f"_{task_name}")


def _task_doc(task_name):
    """任务模块的 docstring，作为工具描述的一部分。"""
    try:
        module = import_module(package="tasks", name=f".{task_name}")
    except Exception:  # noqa: BLE001
        return None
    doc = (module.__doc__ or "").strip()
    return doc or None


def _describe_success(raw_result, result_model):
    """把成功结果翻译成 (result_type, payload)。"""
    if detect_stored_result_type(raw_result) == RESULT_TYPE_S3:
        return RESULT_TYPE_S3, build_s3_result_response(raw_result)

    value = raw_result
    if result_model is not None:
        value = result_model.model_validate(raw_result)
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return RESULT_TYPE_JSON, value


def _do_create(task_name, params_dict, running_id):
    loaded_task = _load_loaded_task(task_name)
    async_result = loaded_task.apply_async(
        args=(),
        kwargs={**params_dict, "fasttask_concurrency_params": None},
        task_id=f"{running_id}-{uuid.uuid4()}",
        queue=task_name,
    )
    return {
        "result_id": async_result.id,
        "state": async_result.state,
        "hint": f"用 check_{task_name}(result_id=...) 查询状态与结果",
    }


def _do_check(task_name, result_id, running_id, result_model):
    if not result_id.startswith(running_id):
        return {
            "result_id": result_id,
            "state": TaskState.failure.value,
            "result_type": RESULT_TYPE_TEXT,
            "result": f"{result_id} 不存在；当前服务实例标识为 {running_id}",
        }

    async_result = celery_app.AsyncResult(result_id)
    state = async_result.state
    traceback_text = async_result.traceback
    raw_result = async_result.result

    if state == TaskState.success.value:
        result_type, payload = _describe_success(raw_result, result_model)
        response = {
            "result_id": result_id,
            "state": state,
            "result_type": result_type,
            "result": payload,
        }
        if result_type == RESULT_TYPE_S3:
            response["hint"] = (
                "结果已外置到对象存储：请用 result.url 下载到本地后再解析。"
                "结果可能很大，不要把整个内容读入上下文。"
            )
        return response

    if state == TaskState.failure.value:
        return {
            "result_id": result_id,
            "state": state,
            "result_type": RESULT_TYPE_TEXT,
            "result": f"{raw_result!r}\n{traceback_text}",
        }

    return {
        "result_id": result_id,
        "state": state,
        "result_type": RESULT_TYPE_TEXT,
        "result": str(raw_result),
    }


def _do_run(task_name, params_dict, result_model, task_id):
    """同步执行任务并返回结果。

    用 Celery 的 ``apply()``（本地立即执行、不走队列）而不是直接调用任务对象：
    直接调用时 ``self.request.id`` 为 None，一旦结果需要外置就会产生
    ``None.json`` 这种互相覆盖的 key。
    """
    loaded_task = _load_loaded_task(task_name)
    eager = loaded_task.apply(
        args=(),
        kwargs={**params_dict, "fasttask_concurrency_params": None},
        task_id=task_id,
    )

    if eager.state != TaskState.success.value:
        return {
            "task_id": task_id,
            "state": eager.state,
            "result_type": RESULT_TYPE_TEXT,
            "result": f"{eager.result!r}\n{eager.traceback}",
        }

    result_type, payload = _describe_success(eager.result, result_model)
    serialized = json.dumps(payload, ensure_ascii=False)
    if len(serialized) > _RUN_INLINE_LIMIT:
        return {
            "task_id": task_id,
            "state": TaskState.success.value,
            "result_type": RESULT_TYPE_TEXT,
            "truncated": True,
            "result": serialized[:1024],
            "hint": (
                f"同步执行的结果约 {len(serialized)} 字节，已截断以避免塞满上下文。"
                f"需要完整结果请改用 create_{task_name} + check_{task_name}"
                "（大结果会自动外置到对象存储并提供下载地址）。"
            ),
        }

    return {
        "task_id": task_id,
        "state": TaskState.success.value,
        "result_type": result_type,
        "result": payload,
    }


def _json(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _flat_signature(model_cls):
    """把 Pydantic 模型摊平为 MCP 工具签名（避免参数被包一层 params）。"""
    return inspect.Signature(
        [
            inspect.Parameter(
                name,
                inspect.Parameter.KEYWORD_ONLY,
                default=inspect.Parameter.empty,
                annotation=Annotated[(field.annotation, field)],
            )
            for name, field in model_cls.model_fields.items()
        ]
    )


# --------------------------------------------------------------------------- #
# 工具注册
# --------------------------------------------------------------------------- #
def _register_create_tool(mcp, task_name, params_model, result_model, running_id_getter):
    async def create_tool(**kwargs) -> str:
        params = params_model(**kwargs) if params_model is not None else kwargs
        running_id = running_id_getter()
        result = await asyncio.to_thread(
            _do_create, task_name, params.model_dump(), running_id
        )
        return _json(result)

    create_tool.__name__ = f"create_{task_name}"
    if params_model is not None:
        create_tool.__signature__ = _flat_signature(params_model)

    doc = _task_doc(task_name)
    lines = [f"创建 {task_name} 异步任务，立即返回 result_id，任务在后台排队执行。"]
    if doc:
        lines.append(f"任务说明：{doc}")
    if get_bool_env("API_CHECK"):
        lines.append(f"用 check_{task_name}(result_id=...) 查询状态与结果。")
    if get_bool_env("API_RUN"):
        lines.append(
            f"若任务可在数秒内完成，也可以用 run_{task_name}(...) 同步拿到结果。"
        )
    mcp.tool(name=create_tool.__name__, description="\n".join(lines))(create_tool)


def _register_check_tool(mcp, task_name, result_model, running_id_getter):
    async def check_tool(result_id: str) -> str:
        running_id = running_id_getter()
        result = await asyncio.to_thread(
            _do_check, task_name, result_id, running_id, result_model
        )
        return _json(result)

    check_tool.__name__ = f"check_{task_name}"
    check_tool.__doc__ = None

    description = (
        f"查询 {task_name} 任务的执行状态与结果。\n"
        "参数 result_id：create 工具返回的任务 ID（服务重启后依然有效）。\n"
        "返回字段：\n"
        "- state：PENDING(排队) / STARTED(执行中) / RETRY(重试) / "
        "SUCCESS(成功) / FAILURE(失败) / REVOKED(已撤销)\n"
        "- result_type：json / s3 / text，决定 result 的含义\n"
        "- result_type=json 时 result 即任务结果；\n"
        "- result_type=s3 时 result 是对象存储引用，其中 result.url 是预签名下载地址；\n"
        "- result_type=text 时 result 为错误信息（失败时含完整 traceback）。\n"
        "\n"
        "重要：结果可能非常大（几十 MB）。当 result_type=s3 时，"
        "务必先把结果下载到本地再解析，不要直接读取内容：\n"
        '  curl -s -o result.json "<result.url>"\n'
        "  jq '.some_field' result.json\n"
        "下载地址有时效，过期后重新调用本工具即可获得新地址。"
    )
    mcp.tool(name=check_tool.__name__, description=description)(check_tool)


def _register_run_tool(mcp, task_name, params_model, result_model, running_id_getter):
    async def run_tool(**kwargs) -> str:
        params = params_model(**kwargs) if params_model is not None else kwargs
        task_id = f"{running_id_getter()}-{uuid.uuid4()}"
        result = await asyncio.to_thread(
            _do_run, task_name, params.model_dump(), result_model, task_id
        )
        return _json(result)

    run_tool.__name__ = f"run_{task_name}"
    if params_model is not None:
        run_tool.__signature__ = _flat_signature(params_model)

    doc = _task_doc(task_name)
    lines = [
        f"同步执行 {task_name} 任务并直接返回结果（不进入任务队列）。",
        "仅适合预计数秒内完成的任务：执行期间会一直占用本次调用，"
        "超出客户端等待时间会失败。耗时任务请改用 "
        f"create_{task_name} + check_{task_name}。",
        "小结果直接返回；大结果会外置到对象存储（result_type=s3，"
        "请下载 result.url 后解析），或按阈值截断并给出提示。",
    ]
    if doc:
        lines.append(f"任务说明：{doc}")
    mcp.tool(name=run_tool.__name__, description="\n".join(lines))(run_tool)


def _register_status_tool(mcp, task_names, running_id_getter):
    async def status_tool() -> str:
        task_infos = (await load_redis_task_infos(task_names)).values()
        end_time = datetime.datetime.now(datetime.timezone.utc)
        payload = {
            "running_id": running_id_getter(),
            "worker_status": await get_worker_status(celery_app),
            "pending_task_count": await get_pending_task_count(task_names=task_names),
            "task_info_total": get_task_statistics_info(
                end_time=end_time, task_infos=task_infos
            ),
        }
        return _json(payload)

    status_tool.__name__ = "fasttask_status"
    mcp.tool(
        name=status_tool.__name__,
        description=(
            "查看 FastTask 服务状态：在线 worker、各任务队列积压数量、"
            "近期成功/失败统计。任务卡住或排查积压时使用。"
        ),
    )(status_tool)


def _register_revoke_tool(mcp, running_id_getter):
    async def revoke_tool(result_id: str) -> str:
        running_id = running_id_getter()
        if not result_id.startswith(running_id):
            return _json(
                {
                    "result_id": result_id,
                    "status": "FAILURE",
                    "message": f"{result_id} 不属于当前服务实例 {running_id}",
                }
            )

        async_result = celery_app.AsyncResult(result_id)
        state = async_result.state
        await asyncio.to_thread(async_result.revoke, terminate=True)
        return _json(
            {
                "result_id": result_id,
                "status": "SUCCESS",
                "message": f"已请求撤销（撤销前状态：{state}）",
            }
        )

    revoke_tool.__name__ = "fasttask_revoke"
    mcp.tool(
        name=revoke_tool.__name__,
        description=(
            "撤销一个排队中或执行中的任务。\n"
            "参数 result_id：create 工具返回的任务 ID。"
        ),
    )(revoke_tool)


# --------------------------------------------------------------------------- #
# 构建入口
# --------------------------------------------------------------------------- #
def build_mcp_server(task_names, running_id_getter):
    """构建 FastMCP 实例，并按 API_* 开关动态注册任务工具。"""
    from mcp.server.fastmcp import FastMCP

    mcp = FastMCP(
        MCP_SERVER_NAME,
        instructions=_MCP_INSTRUCTIONS,
        stateless_http=True,  # 多 uvicorn worker 下必须无状态
        json_response=True,  # 纯 JSON 响应，避免 SSE 被中间件缓冲
    )
    # 挂载到 FastAPI 的 /mcp 下，子应用内部路径从根开始
    mcp.settings.streamable_http_path = "/"

    enable_create = get_bool_env("API_CREATE")
    enable_check = get_bool_env("API_CHECK")
    enable_run = get_bool_env("API_RUN")

    for task_name in task_names:
        params_model = _load_task_model(task_name, "Params")
        result_model = _load_task_model(task_name, "Result")

        if enable_create:
            _register_create_tool(
                mcp, task_name, params_model, result_model, running_id_getter
            )
        if enable_check:
            _register_check_tool(mcp, task_name, result_model, running_id_getter)
        if enable_run:
            _register_run_tool(
                mcp, task_name, params_model, result_model, running_id_getter
            )

    if get_bool_env("API_STATUS_INFO"):
        _register_status_tool(mcp, task_names, running_id_getter)
    if get_bool_env("API_REVOKE"):
        _register_revoke_tool(mcp, running_id_getter)

    return mcp


def _load_task_model(task_name, name):
    """取任务模块里的 Params / Result 模型；属性不存在时返回 None。

    导入失败不吞异常：任务模块加载不了意味着服务本身有问题，
    与 api.py 注册路由时的行为保持一致（在这里就报错，
    而不是默默生成一个“参数为空的工具”让人去猜）。
    """
    module = import_module(package="tasks", name=f".{task_name}")
    return getattr(module, name, None)


# --------------------------------------------------------------------------- #
# 认证
# --------------------------------------------------------------------------- #
class MCPAuthMiddleware:
    """MCP 端点认证：复用 FastTask 既有的 HTTP Basic 凭据。

    行为与 HTTP 接口保持一致：
    - ``user_to_passwd.json`` 不存在或为空 → 匿名放行（不要求带头）
    - 否则要求 ``Authorization: Basic ...``，凭据与其它接口完全相同
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        user_to_passwd = load_user_to_passwd()
        if not user_to_passwd:
            await self.app(scope, receive, send)
            return

        if self._authenticated(scope, user_to_passwd):
            await self.app(scope, receive, send)
            return

        from starlette.responses import JSONResponse

        response = JSONResponse(
            {"detail": "Not authenticated"},
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="fasttask"'},
        )
        await response(scope, receive, send)

    @staticmethod
    def _authenticated(scope, user_to_passwd) -> bool:
        from starlette.datastructures import Headers

        auth_header = Headers(scope=scope).get("authorization", "")
        if not auth_header.startswith("Basic "):
            return False
        try:
            decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
            username, _, password = decoded.partition(":")
        except Exception:  # noqa: BLE001
            return False
        expected = user_to_passwd.get(username)
        return bool(expected) and secrets.compare_digest(
            password.encode("utf8"), expected.encode("utf8")
        )


def wrap_mcp_app(app):
    """给 MCP 子应用套上认证中间件。"""
    return MCPAuthMiddleware(app)
