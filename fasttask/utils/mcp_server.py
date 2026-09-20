"""MCP 适配层：把 FastTask 既有的 HTTP 接口翻译成 MCP 工具。

设计原则
--------
- **纯翻译层**：不引入新的状态或机制。任务队列、状态存储、并发控制、
  超时与撤销仍然全部由 FastTask 自己负责。
- **业务逻辑不重复**：创建 / 查询 / 同步执行都走 ``utils/task_ops.py``，
  与 HTTP 接口共用同一套实现，避免两条通道语义漂移。
- **工具集合跟随 API_* 开关**：``API_CREATE`` / ``API_CHECK`` / ``API_RUN``
  决定注册哪些工具，描述也随之调整，保证“描述里提到的能力”一定可用。
- **返回值保持轻量**：``check_*`` 只回状态与结果引用，大结果由调用方
  按预签名地址自行下载，避免灌进模型上下文。
- **无状态传输**：``stateless_http=True``。``UVICORN_WORKERS`` 默认为 2，
  有状态模式会让会话散落在不同 worker 上而随机返回 404 Session not found。
"""

import asyncio
import base64
import datetime
import inspect
import json
import secrets
from typing import Annotated

from celery_app import app as celery_app

from utils.api_utils import (
    get_pending_task_count,
    get_task_statistics_info,
    get_worker_status,
    load_redis_task_infos,
    load_user_to_passwd,
)
from utils.result_storage import ResultType
from utils.task_ops import (
    check_task,
    create_task,
    load_task_model,
    new_task_id,
    run_task_sync,
    task_doc,
)
from utils.tools import get_bool_env


MCP_PATH = "/mcp"
MCP_SERVER_NAME = "fasttask"


# MCP 的说明分两层：instructions（全局一份，讲“这个模块是什么、平台怎么用”）
# 与 tools[].description（每个工具一份，讲“这个任务干什么”）。
# 下面这段是平台约定，对所有 FastTask 封装的模块都一样。
# MCP 的说明分两层：instructions（全局一份）与 tools[].description（每个工具一份）。
#
# instructions 的排列顺序按「AI 需要什么才能正确行动」决定，而不是按信息类型：
# 客户端普遍只展示它的前几百字符（pi-mcp-adapter 的阈值是 300），所以决定调用行为的
# 规则必须放在最前面；背景介绍放后面 —— 读不到也不影响正确调用。

# 核心调用约定。放最前面，保证进入可见窗口：AI 能不能正确调用，取决于这几行。
_CALLING_CONVENTIONS = """调用方式：默认走 create_<task> + check_<task>，后台执行、不占用本次调用。
run_<task> 只在需要立刻拿到结果时用（不产生可查询的 id，结果一次性消费）。
通用接口：fasttask_status 查看 worker 与队列、fasttask_revoke 用 result_id 撤销任务。"""

# 使用细节。排在核心规则之后：属于「用起来才会碰到」的信息，即便被客户端截断，
# 工具描述里也各有兜底，不会导致调用错误。
_USAGE_DETAILS = """典型流程：create_<task> 拿到 result_id → check_<task> 轮询 → 取回结果。

结果形态（result_type 决定 result 的含义）：
- json：result 即任务结果，可直接使用
- s3  ：结果已外置到对象存储，result 是引用（含预签名 url）
- text：result 是错误信息（失败时含完整 traceback）或状态文本

下载外置结果：result.url 就是带签名的下载地址，看开头是不是 http 即可判断形态：
    完整地址   curl -s -o result.json "<url>"
    相对路径   curl -s -o result.json "https://<服务地址><url>"   # 拼上配置本服务时的地址
下载后用 jq 等工具按需提取字段，不要把整个结果读入上下文；预签名地址有时效，
过期后重新调用查询接口即可拿到新地址。"""


# --------------------------------------------------------------------------- #
# instructions 的组装（顺序即优先级，见上方说明）
# --------------------------------------------------------------------------- #
def _first_paragraph(text):
    """取 docstring 的首段（遇到空行即停）作为一句话摘要。

    docstring 常写成“首段一句话 + 空行 + 详细说明”，首段正好适合当任务一览。
    """
    if not text:
        return ""
    got = []
    for line in text.splitlines():
        if not line.strip():
            break
        got.append(line.strip())
    return " ".join(got)


# project_summary 紧跟模块名出现在 instructions 最前面，而客户端普遍只展示
# instructions 的前几百字符（pi 的阈值是 300）。summary 过长会把「怎么调用」的
# 规则挤出可见窗口，所以超长时启动告警 —— 不报错，因为这只是可读性问题。
_SUMMARY_MAX_LENGTH = 100


def warn_if_summary_too_long():
    """setting.project_summary 过长时告警，提醒它挤占了 instructions 的可见窗口。"""
    summary = module_field("project_summary")
    if len(summary) > _SUMMARY_MAX_LENGTH:
        # flush=True 必需：这个进程的 stdout 常被重定向到文件/管道（supervisord、nohup），
        # 此时是块缓冲，不 flush 的话告警会卡在缓冲区里，等进程退出才落盘 ——
        # 而那正是我们最需要看到它的时点之后。
        print(
            f"FastTask ---> [warn] setting.project_summary 长度 {len(summary)} "
            f"超过 {_SUMMARY_MAX_LENGTH}：它位于 MCP instructions 最前面，过长会"
            "把「怎么调用」的规则挤出客户端的可见窗口（多数客户端只展示前几百字符）。"
            "建议压成一句话定位。",
            flush=True,
        )


def module_field(name, prefix=""):
    """取模块自己 setting.py 里的一个字段；缺失或导入失败时返回空串。

    以前 setting.py 只喂给 FastAPI/Swagger，对 MCP 客户端不可见，导致 AI 打开
    这个服务时不知道“整体是干什么的”，只能从单个任务的 docstring 拼凑。
    这里拆成字段分别摆放，好让 summary 紧跟标题、description 靠后。
    """
    try:
        import setting
    except Exception:  # noqa: BLE001 - 模块信息缺失不应影响工具可用性
        return ""
    value = str(getattr(setting, name, "") or "").strip()
    return f"{prefix}{value}" if value else ""


def build_task_catalog(task_names):
    """任务一览：每个任务一行摘要，取模块 docstring 的首段。"""
    if not task_names:
        return ""
    out = ["本模块提供以下任务："]
    for name in task_names:
        summary = _first_paragraph(task_doc(name))
        out.append(f"- {name}：{summary}" if summary else f"- {name}")
    return "\n".join(out)


def build_instructions(task_names):
    """拼装 MCP instructions。

    顺序按「AI 需要什么才能正确行动」排：模块名与一句话定位 → 调用规则 → 使用细节
    → 模块详细说明与版本 → 任务一览。客户端只展示前几百字符，所以规则必须靠前。
    """
    blocks = [
        module_field("project_title"),
        module_field("project_summary"),
        _CALLING_CONVENTIONS,
        _USAGE_DETAILS,
        module_field("project_description"),
        module_field("project_version", prefix="版本："),
        build_task_catalog(task_names),
    ]
    return "\n\n".join(block for block in blocks if block)


def _json(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _flat_signature(model_cls, return_annotation=inspect.Parameter.empty):
    """把 Pydantic 模型摊平为 MCP 工具签名（避免参数被包一层 params）。

    注意：设置了 ``__signature__`` 之后，``inspect.signature()`` 会以它为准，
    而 FastMCP 正是从这里读返回类型来生成 outputSchema —— 所以返回注解必须
    在同一个 Signature 上带出去，否则只改 ``__annotations__`` 不生效。
    """
    return inspect.Signature(
        [
            inspect.Parameter(
                name,
                inspect.Parameter.KEYWORD_ONLY,
                default=inspect.Parameter.empty,
                annotation=Annotated[(field.annotation, field)],
            )
            for name, field in model_cls.model_fields.items()
        ],
        return_annotation=return_annotation,
    )


# --------------------------------------------------------------------------- #
# 工具注册
# --------------------------------------------------------------------------- #
def _build_output_model(task_name, result_model):
    """构造工具返回值的 Pydantic 模型，让 MCP 生成有意义的 outputSchema。

    FastMCP 只从返回类型注解生成 outputSchema，而 FastTask 的返回结构是动态的，
    所以这里动态建一个模型。外层字段固定；result 的形态由 result_type 决定：

    - json：任务自己定义的 Result 模型
    - s3  ：对象存储引用（含预签名 url）
    - text：错误信息/状态文本
    """
    from typing import Literal, Union

    from pydantic import Field, create_model

    result_types = tuple(t.value for t in ResultType)
    # result 在 json 时是任务的 Result 模型，s3 时是引用字典，text 时是字符串。
    # 用 Union 让 outputSchema 里能看到任务真实的返回结构，而不是笼统一个 object。
    if result_model is not None:
        result_annotation = Union[result_model, dict, str]
    else:
        result_annotation = Union[dict, str]

    return create_model(
        f"{task_name}_ToolResult",
        result_id=(
            str,
            Field(
                default="",
                description=(
                    "异步任务 ID。create_* 返回后用它去 check_*；"
                    "run_* 为同步执行，该字段为空且不可用于 check"
                ),
            ),
        ),
        state=(
            str,
            Field(
                description=(
                    "任务状态：PENDING(排队) / STARTED(执行中) / RETRY(重试) / "
                    "SUCCESS(成功) / FAILURE(失败) / REVOKED(已撤销)"
                )
            ),
        ),
        result_type=(
            Literal[result_types],
            Field(
                description=(
                    "决定 result 的含义：json=任务结果本身；"
                    "s3=结果已外置，result 是含预签名 url 的引用；"
                    "text=错误信息或状态文本"
                )
            ),
        ),
        result=(
            result_annotation,
            Field(
                description=(
                    "任务结果。result_type=json 时为 anyOf 中第一个结构；"
                    "s3 时为 {size_bytes, sha256, url, expires_at} 引用对象"
                    "（url 是可直接下载的地址：可能是完整 URL，也可能是相对路径，"
                    "后者需拼上服务地址）；text 时为字符串（错误信息或状态说明）"
                )
            ),
        ),
    )


def _as_structured(tool_fn, task_name, result_model):
    """把工具函数的返回注解换成动态模型。

    这样 FastMCP 会生成 outputSchema，并把返回值同时作为结构化内容返回，
    调用方不必先跑一次来猜返回形状。
    """
    tool_fn.__annotations__["return"] = _build_output_model(task_name, result_model)
    return tool_fn


def _primary_tool(task_name):
    """本任务用哪个工具承载完整的业务说明。

    任务 docstring 常有几百字符，而每个任务会生成 3 个工具。三份全文既浪费上下文，
    也让注意力被重复内容分散，所以只在「主工具」上给全文，其余给一行摘要。
    主工具优先取 create（默认入口）；未启用时依次退到 run、check，
    保证无论模块怎么配，任务说明总有一处能看到全文。
    """
    for flag, name in (
        ("API_CREATE", f"create_{task_name}"),
        ("API_RUN", f"run_{task_name}"),
        ("API_CHECK", f"check_{task_name}"),
    ):
        if get_bool_env(flag):
            return name
    return ""


def _task_business_lines(task_name, tool_name):
    """任务自身的业务说明：主工具给全文，其余给一行摘要。"""
    doc = task_doc(task_name)
    if not doc:
        return []
    primary = _primary_tool(task_name)
    if tool_name == primary:
        return [f"任务说明：{doc}"]
    hint = f"（完整说明见 {primary}）" if primary else ""
    return [f"该任务：{_first_paragraph(doc)}{hint}"]


def _register_create_tool(mcp, task_name, params_model, result_model, running_id_getter):
    async def create_tool(**kwargs) -> dict:
        params = params_model(**kwargs) if params_model is not None else kwargs
        payload = await asyncio.to_thread(
            create_task, task_name, params.model_dump(), running_id_getter()
        )
        return payload

    create_tool.__name__ = f"create_{task_name}"
    if params_model is not None:
        create_tool.__signature__ = _flat_signature(
            params_model, _build_output_model(task_name, result_model)
        )
    else:
        _as_structured(create_tool, task_name, result_model)

    # 工具描述只讲"这个工具做什么"，平台约定（优先 create、result_id 用法、
    # run 的代价、结果形态与下载）统一放 instructions，避免每个工具重复一遍。
    # 任务自身的业务说明只在本任务的一个工具上给全文，其余给一行摘要 ——
    # 否则同一份 500+ 字符的 docstring 会在 create/check/run 里各出现一次。
    lines = [f"创建 {task_name} 异步任务（默认入口）。"]
    lines.extend(_task_business_lines(task_name, create_tool.__name__))
    mcp.tool(name=create_tool.__name__, description="\n".join(lines))(create_tool)


def _register_check_tool(mcp, task_name, result_model, running_id_getter):
    async def check_tool(result_id: str) -> dict:
        payload = await asyncio.to_thread(
            check_task, result_id, running_id_getter(), result_model
        )
        return payload

    check_tool.__name__ = f"check_{task_name}"
    _as_structured(check_tool, task_name, result_model)

    lines = [f"查询 {task_name} 任务的状态与结果。"]
    lines.extend(_task_business_lines(task_name, check_tool.__name__))
    mcp.tool(name=check_tool.__name__, description="\n".join(lines))(check_tool)


def _register_run_tool(mcp, task_name, params_model, result_model, running_id_getter):
    async def run_tool(**kwargs) -> dict:
        params = params_model(**kwargs) if params_model is not None else kwargs
        payload = await asyncio.to_thread(
            run_task_sync,
            task_name,
            params.model_dump(),
            new_task_id(running_id_getter()),
            result_model,
        )
        return payload

    run_tool.__name__ = f"run_{task_name}"
    if params_model is not None:
        run_tool.__signature__ = _flat_signature(
            params_model, _build_output_model(task_name, result_model)
        )
    else:
        _as_structured(run_tool, task_name, result_model)

    doc = task_doc(task_name)
    lines = [f"同步执行 {task_name} 并直接返回结果（结果随本次响应返回）。"]
    if get_bool_env("API_CREATE"):
        # 这里写“优先 create”不是上层知识的重复，而是本工具自身的适用性说明：
        # “什么时候该用我、什么时候不该用我”。实战验证过——只把它放在
        # instructions 里无效，因为客户端普遍只展示 instructions 的前几百字符
        # （pi 的阈值是 300），而 AI 一定会读 describe 的结果。
        lines.append(
            f"优先使用 create_{task_name} + check_{task_name}；"
            "本工具仅当你需要立刻拿到结果时使用，且不产生可查询的任务 id。"
        )
    lines.extend(_task_business_lines(task_name, run_tool.__name__))
    mcp.tool(name=run_tool.__name__, description="\n".join(lines))(run_tool)


def _register_status_tool(mcp, task_names, running_id_getter):
    async def status_tool() -> str:
        task_infos = (await load_redis_task_infos(task_names)).values()
        end_time = datetime.datetime.now(datetime.timezone.utc)
        return _json(
            {
                "running_id": running_id_getter(),
                "worker_status": await get_worker_status(celery_app),
                "pending_task_count": await get_pending_task_count(task_names),
                "task_info_total": get_task_statistics_info(
                    end_time=end_time, task_infos=task_infos
                ),
            }
        )

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


def build_mcp_server(task_names, running_id_getter):
    """构建 FastMCP 实例，并按 API_* 开关动态注册任务工具。"""
    from mcp.server.fastmcp import FastMCP
    from mcp.server.transport_security import TransportSecuritySettings

    warn_if_summary_too_long()

    mcp = FastMCP(
        MCP_SERVER_NAME,
        instructions=build_instructions(task_names),
        stateless_http=True,  # 多 uvicorn worker 下必须无状态
        json_response=True,  # 纯 JSON 响应，避免 SSE 被中间件缓冲
        # FastMCP 默认的 DNS rebinding 保护只放行 localhost，而 FastTask 实际
        # 部署在容器/K8s/反代后面，客户端只用 IP 或域名访问，任何非本机请求
        # 都会被拦成 421 Invalid Host header，MCP 等于不可用。API 层本身
        # 已有认证（或按内部约定无需认证），这里不再叠加 Host 白名单。
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=False
        ),
    )
    # 挂载到 FastAPI 的 /mcp 下，子应用内部路径从根开始
    mcp.settings.streamable_http_path = "/"

    enable_create = get_bool_env("API_CREATE")
    enable_check = get_bool_env("API_CHECK")
    enable_run = get_bool_env("API_RUN")

    for task_name in task_names:
        params_model = load_task_model(task_name, "Params")
        result_model = load_task_model(task_name, "Result")

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


# --------------------------------------------------------------------------- #
# 认证（复用 FastTask 既有的 HTTP Basic 凭据）
# --------------------------------------------------------------------------- #
class MCPAuthMiddleware:
    """行为与其它 HTTP 接口保持一致：

    - ``user_to_passwd.json`` 不存在或为空 → 匿名放行（不要求带头）
    - 否则要求 ``Authorization: Basic ...``，凭据完全相同
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        user_to_passwd = load_user_to_passwd()
        if not user_to_passwd or self._authenticated(scope, user_to_passwd):
            await self.app(scope, receive, send)
            return

        from starlette.responses import JSONResponse

        await JSONResponse(
            {"detail": "Not authenticated"},
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="fasttask"'},
        )(scope, receive, send)

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
