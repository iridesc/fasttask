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
    RESULT_TYPE_JSON,
    RESULT_TYPE_S3,
    RESULT_TYPE_TEXT,
    build_s3_result_response,
    detect_stored_result_type,
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


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    print("Application startup: Initializing RUNNING_ID...")
    app.state.RUNNING_ID = await initialize_running_id()
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
    app.add_middleware(
        SelectiveGZipMiddleware,
        minimum_size=1000,
        compresslevel=int(os.environ["RESPONSE_COMPRESS_LEVEL"]),
    )

if get_bool_env("DEBUG"):
    app.add_middleware(LoggingMiddleware)

if get_bool_env("FLOWER_ENABLED"):
    app.add_middleware(FlowerProxyMiddleware)

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

    task = getattr(
        import_module(package="loaded_tasks", name=f".{task_name}"), f"_{task_name}"
    )

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
        result_type: Literal["json", "s3", "text"] = RESULT_TYPE_JSON
        result: Any = ""

    if get_bool_env("API_RUN"):

        @app.post(f"/run/{task_name}", response_model=ResultInfo, tags=task_base_tag)
        def run(
            params: FullParams, username: Annotated[str, Depends(get_current_username)]
        ):

            try:
                result = Result.model_validate(task(**params.model_dump()))
                state = TaskState.success.value
                result_type = RESULT_TYPE_JSON
            except Exception:
                result = traceback.format_exc()
                state = TaskState.failure.value
                result_type = RESULT_TYPE_TEXT

            return ResultInfo(result=result, state=state, result_type=result_type)

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
                async_result = task.apply_async(
                    args=(),
                    kwargs=params.model_dump(),
                    task_id=f"{app.state.RUNNING_ID}-{uuid.uuid4()}",
                    queue=task_name,
                )
            except Exception:
                result_info = ResultInfo(result=traceback.format_exc())
            else:
                result_info = ResultInfo(
                    id=async_result.id, state=async_result.state, result=""
                )

            return result_info

    if get_bool_env("API_CHECK"):

        @app.get(
            f"/check/{task_name}",
            response_model=ResultInfo,
            tags=task_base_tag,
        )
        def check(
            result_id: str, username: Annotated[str, Depends(get_current_username)]
        ):
            if not result_id.startswith(app.state.RUNNING_ID):
                return ResultInfo(
                    id=result_id,
                    state=TaskState.failure.value,
                    result_type=RESULT_TYPE_TEXT,
                    result=f"{result_id=} not exist, current {app.state.RUNNING_ID=}",
                )

            async_result = celery_app.AsyncResult(result_id)

            # 立即获取 状态以及数据 尽量避免不一致的情况
            state = async_result.state
            traceback = async_result.traceback
            result = async_result.result

            if state == TaskState.success.value:
                if detect_stored_result_type(result) == RESULT_TYPE_S3:
                    # 内容已在对象存储：只回引用 + 预签名地址，不回内容
                    result = build_s3_result_response(result)
                    result_type = RESULT_TYPE_S3
                else:
                    result = Result.model_validate(result)
                    result_type = RESULT_TYPE_JSON
            elif state == TaskState.failure.value:
                result = f"{result=} {traceback=}"
                result_type = RESULT_TYPE_TEXT
            else:
                result = str(result)
                result_type = RESULT_TYPE_TEXT

            return ResultInfo(
                id=result_id, state=state, result_type=result_type, result=result
            )


for task_name in LOADED_TASKS:
    get_task_apis(task_name)
