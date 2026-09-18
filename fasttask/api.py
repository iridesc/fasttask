import contextlib
import datetime
import os
import sys
import uuid
import traceback
import asyncio
from enum import Enum
from typing import Any, Literal, Annotated, Optional
from importlib import import_module

from utils.tools import get_list_env, get_bool_env
from pydantic import BaseModel, Field
from starlette.responses import FileResponse
from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.openapi.docs import get_swagger_ui_html
from celery_app import app as celery_app
from utils.api_utils import (
    TaskState,
    check_file_name,
    get_current_username,
    get_pending_task_count,
    get_task_statistics_info,
    get_worker_status,
    initialize_running_id,
    load_redis_task_infos,
    try_import_Data,
    upload_sync,
    FlowerProxyMiddleware,
    SelectiveGZipMiddleware,
    LoggingMiddleware,
)
from utils.result_storage import (
    ResultType,
    ensure_bucket,
    get_bucket,
    get_s3_endpoint,
    is_s3_enabled,
)
from utils.s3_proxy import S3ProxyMiddleware
from utils.task_ops import (
    check_task,
    create_task,
    new_task_id,
    run_task_sync,
)
from setting import project_title, project_description, project_summary, project_version


sys.path.append("tasks")

LOADED_TASKS = get_list_env("LOADED_TASKS")
CONF_DIR = os.environ["CONF_DIR"]


class ActionStatus(Enum):
    success = "SUCCESS"
    failure = "FAILURE"


class ActionResp(BaseModel):
    status: ActionStatus = ActionStatus.failure
    result: Any = ""
    message: str = ""


class DownloadFileInfo(BaseModel):
    file_name: str = "lp.jpg"


class ResultIDParams(BaseModel):
    result_id: str


ALLOWED_STATUS_FIELDS = Literal[
    "worker_status",
    "task_info",
    "pending_task_count",
]


class StatusInfoQueryParams(BaseModel):
    fields: list[ALLOWED_STATUS_FIELDS] = []


class ConcurrencyParams(BaseModel):
    concurrency_key: str = Field(..., description="并发控制的key")
    max_concurrency: int = Field(default=16, gt=0, description="最大并发量")
    countdown: int = Field(default=60, description="退避时间（秒）")
    expire: int = Field(default=30 * 60, description="锁的过期时间（秒）避免死锁")


class BaseConcurrencyParams(BaseModel):
    fasttask_concurrency_params: Optional[ConcurrencyParams] = Field(
        None, description="并发参数"
    )


def app_state_running_id():
    """延迟读取 RUNNING_ID：MCP 工具被调用时 FastAPI app 已创建完成。"""
    return app.state.RUNNING_ID


# MCP 端点（可选）：把既有接口翻译成 MCP 工具，不引入新的状态或机制
_mcp_server = None
_mcp_app = None
if get_bool_env("API_MCP"):
    try:
        from utils.mcp_server import MCP_PATH, build_mcp_server, wrap_mcp_app
    except ImportError as error:
        raise RuntimeError(
            "API_MCP=True 需要 mcp 依赖，请安装：pip install 'mcp>=1.30,<2'"
        ) from error

    _mcp_server = build_mcp_server(
        LOADED_TASKS, running_id_getter=lambda: app_state_running_id()
    )
    _mcp_app = wrap_mcp_app(_mcp_server.streamable_http_app())


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    print("Application startup: Initializing RUNNING_ID...")
    app.state.RUNNING_ID = await initialize_running_id()
    if is_s3_enabled():
        # 对象存储自检：配置错误在这里就暴露，而不是等任务跑完上传时才失败
        await asyncio.to_thread(ensure_bucket)

    if _mcp_server is not None:
        # FastAPI 不会自动运行 mount 子应用的 lifespan，流式会话管理器必须显式托管
        async with _mcp_server.session_manager.run():
            yield
    else:
        yield


app = FastAPI(
    title=project_title,
    description=project_description,
    summary=project_summary,
    version=project_version,
    docs_url=None,
    lifespan=lifespan,
)



# 响应压缩：默认开启（RESPONSE_COMPRESS=False 关闭），压缩级别由 RESPONSE_COMPRESS_LEVEL 控制。
# - 仅在客户端声明 Accept-Encoding: gzip 时生效，向后兼容；
# - 压缩在线程池中执行（zlib 压缩期间释放 GIL），不会阻塞事件循环；
# - /download、/flower 已在 SelectiveGZipMiddleware 中排除。
# - 必须最先注册（位于最内层、紧贴路由）：外层是 BaseHTTPMiddleware
#   （FlowerProxyMiddleware / LoggingMiddleware），它们会把响应拆成
#   more_body=True 的分块再转发。GZip 若在外层就只能看到这种"伪流式"响应，
#   既要多缓冲一份完整 body，还会被 max_buffer 上限误伤。
if get_bool_env("RESPONSE_COMPRESS"):
    # 对象存储响应直传（大文件无收益），与 /download、/flower 一同跳过压缩
    exclude_prefixes = ["/download", "/flower"]
    if is_s3_enabled():
        exclude_prefixes.append(f"/{get_bucket()}/")
    app.add_middleware(
        SelectiveGZipMiddleware,
        minimum_size=1000,
        compresslevel=int(os.environ["RESPONSE_COMPRESS_LEVEL"]),
        exclude_prefixes=tuple(exclude_prefixes),
    )

if get_bool_env("DEBUG"):
    app.add_middleware(LoggingMiddleware)

if get_bool_env("FLOWER_ENABLED"):
    app.add_middleware(FlowerProxyMiddleware)

if _mcp_app is not None:
    # MCP 子应用自带认证（复用 FastTask 既有的 HTTP Basic 凭据）
    app.mount(MCP_PATH, _mcp_app)

if is_s3_enabled():
    # 最后注册 = 最外层：对象存储请求直接转发，不进入 gzip / 日志等中间件。
    # endpoint 与预签名的 Host 同源，因此客户端用哪个地址访问都能验签通过。
    app.add_middleware(
        S3ProxyMiddleware,
        bucket=get_bucket(),
        endpoint=get_s3_endpoint(),
    )

if get_bool_env("API_DOCS"):

    @app.get("/docs", include_in_schema=False)
    async def custom_swagger_ui_html(
        username: Annotated[str, Depends(get_current_username)],
    ):
        return get_swagger_ui_html(openapi_url=app.openapi_url, title=app.title)


if get_bool_env("API_STATUS_INFO"):

    @app.post("/status_info", tags=["Monitoring"])
    async def status_info(
        username: Annotated[str, Depends(get_current_username)],
        params: StatusInfoQueryParams,
    ):
        task_infos = (
            await load_redis_task_infos(LOADED_TASKS)
            if "task_infos" in params.fields
            else dict()
        ).values()
        worker_status = (
            await get_worker_status(celery_app)
            if "worker_status" in params.fields
            else dict()
        )

        end_time = datetime.datetime.now(datetime.timezone.utc)

        status_info = {
            "running_id": app.state.RUNNING_ID,
            "username": username,
            "worker_status": worker_status,
            "task_info_total": get_task_statistics_info(
                end_time=end_time, task_infos=task_infos
            )
            if "task_info" in params.fields
            else dict(),
            "pending_task_count": await get_pending_task_count(task_names=LOADED_TASKS)
            if "pending_task_count" in params.fields
            else dict(),
        }

        for task_name in LOADED_TASKS:
            status_info[f"task_info_{task_name}"] = (
                get_task_statistics_info(
                    end_time=end_time, task_infos=task_infos, task_name=task_name
                )
                if "task_info" in params.fields
                else dict()
            )

        return status_info


if get_bool_env("API_FILE_DOWNLOAD"):

    @app.get("/download", tags=["File Management"])
    def download(file_name, username: Annotated[str, Depends(get_current_username)]):
        validated_file_path = check_file_name(file_name, username)
        if not os.path.isfile(validated_file_path):
            return HTTPException(status_code=404, detail="File not found")
        display_filename = os.path.basename(validated_file_path)
        print(f"{username=}: 下载: {display_filename=} {validated_file_path=}")
        return FileResponse(validated_file_path, filename=display_filename)


if get_bool_env("API_FILE_UPLOAD"):

    @app.post("/upload", tags=["File Management"])
    async def upload(
        file: UploadFile, username: Annotated[str, Depends(get_current_username)]
    ):
        file_name = await asyncio.to_thread(upload_sync, file, username)
        print(f"{username=}: 上传: {file.filename=} -> {file_name}")
        return {"file_name": file_name}


if get_bool_env("API_REVOKE"):

    @app.post("/revoke", response_model=ActionResp, tags=["Task Control"])
    def revoke(
        result_id_params: ResultIDParams,
        username: Annotated[str, Depends(get_current_username)],
    ):
        resp = ActionResp()
        result_id = result_id_params.result_id
        if not result_id.startswith(app.state.RUNNING_ID):
            resp.message = f"invalid {result_id=} current {app.state.RUNNING_ID=}"
            return resp

        async_result = celery_app.AsyncResult(result_id)

        state = async_result.state
        async_result.revoke(terminate=True)

        if state in [
            TaskState.success.value,
            TaskState.failure.value,
            TaskState.revoked.value,
        ]:
            resp.message = "task ended or revoked already"
            resp.status = ActionStatus.success

        elif state == TaskState.pending.value:
            resp.message = "task is still pending, will revoked later"
            resp.status = ActionStatus.success

        elif state == TaskState.started.value:
            resp.message = "task started, revoking now"
            resp.status = ActionStatus.success

        elif state == TaskState.retry.value:
            resp.message = "task retrying, revoking now"
            resp.status = ActionStatus.success

        else:
            resp.message = f"unknown task state {state=}"
            resp.status = ActionStatus.failure

        return resp


def get_task_apis(task_name):
    task_base_tag = [f"Task: {task_name}"]

    # 预加载任务模块：任务无法导入时在启动阶段就暴露，而不是等到首次调用
    import_module(package="loaded_tasks", name=f".{task_name}")

    task_model = import_module(package="tasks", name=f".{task_name}")
    Result = try_import_Data(task_model, "Result")
    Params = try_import_Data(task_model, "Params")

    class FullParams(BaseConcurrencyParams, Params):
        pass

    class ResultInfo(BaseModel):
        id: str = ""
        state: TaskState = TaskState.failure.value
        # json: result 为任务定义的 Result 模型（历史行为）
        # s3:   result 为对象存储引用（含预签名下载地址）
        # text: result 为错误信息或状态字符串
        result_type: Literal["json", "s3", "text"] = ResultType.json.value
        result: Any = ""

    def as_result_info(payload: dict) -> ResultInfo:
        """把 task_ops 的返回结构转成 HTTP 响应模型。"""
        return ResultInfo(
            id=payload.get("result_id", ""),
            state=payload["state"],
            result_type=payload["result_type"],
            result=payload["result"],
        )

    def failure_payload() -> dict:
        return {
            "state": TaskState.failure.value,
            "result_type": ResultType.text.value,
            "result": traceback.format_exc(),
        }

    if get_bool_env("API_RUN"):

        @app.post(f"/run/{task_name}", response_model=ResultInfo, tags=task_base_tag)
        def run(
            params: FullParams, username: Annotated[str, Depends(get_current_username)]
        ):
            return as_result_info(
                run_task_sync(
                    task_name,
                    params.model_dump(),
                    new_task_id(app.state.RUNNING_ID),
                    Result,
                )
            )

    if get_bool_env("API_CREATE"):

        @app.post(
            f"/create/{task_name}",
            response_model=ResultInfo,
            tags=task_base_tag,
        )
        def create(
            params: FullParams,
            username: Annotated[str, Depends(get_current_username)],
        ):
            try:
                payload = create_task(
                    task_name, params.model_dump(), app.state.RUNNING_ID
                )
            except Exception:
                payload = failure_payload()

            return as_result_info(payload)

    if get_bool_env("API_CHECK"):

        @app.get(
            f"/check/{task_name}",
            response_model=ResultInfo,
            tags=task_base_tag,
        )
        def check(
            result_id: str, username: Annotated[str, Depends(get_current_username)]
        ):
            return as_result_info(check_task(result_id, app.state.RUNNING_ID, Result))


for task_name in LOADED_TASKS:
    get_task_apis(task_name)
