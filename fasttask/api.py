import contextlib
import datetime
import json
import os
import sys
import uuid
import traceback
import asyncio
from enum import Enum
from typing import Any, Literal, Union, Annotated, Optional
from importlib import import_module

from utils.tools import get_list_env, get_bool_env
from pydantic import BaseModel, Field
from starlette.responses import FileResponse
from fastapi import Depends, FastAPI, HTTPException, UploadFile
from fastapi.openapi.docs import get_swagger_ui_html
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
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
    redis_params,
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


def truncate_body(body: bytes, max_len: int = 200) -> str:
    try:
        text = body.decode("utf-8", errors="replace")
    except:
        text = body.decode("latin-1", errors="replace")

    if len(text) > max_len:
        return f"{text[:max_len]}... [truncated]"
    return text


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        print(f"\n{'=' * 50}")
        print(f"📥 Request: {request.method} {request.url}")
        print(f"   Headers: {dict(request.headers)}")

        body = await request.body()
        if body:
            content_type = request.headers.get("content-type", "")
            if "application/json" in content_type:
                try:
                    print(f"   Body: {json.loads(body)}")
                except:
                    print(f"   Body: {truncate_body(body)}")
            else:
                print(f"   Body: {truncate_body(body)}")

        async def receive():
            return {"type": "http.request", "body": body}

        request._receive = receive

        response = await call_next(request)

        response_body = b""
        async for chunk in response.body_iterator:
            response_body += chunk

        print(f"📤 Response: {response.status_code}")
        print(f"   Headers: {dict(response.headers)}")

        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                print(f"   Body: {json.loads(response_body)}")
            except:
                print(f"   Body: {truncate_body(response_body)}")
        else:
            print(f"   Body: {truncate_body(response_body)}")

        print(f"{'=' * 50}\n")

        return Response(
            content=response_body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )


if get_bool_env("DEBUG"):
    app.add_middleware(LoggingMiddleware)


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
        result: Union[Result, str]

    if get_bool_env("API_RUN"):

        @app.post(f"/run/{task_name}", response_model=ResultInfo, tags=task_base_tag)
        def run(
            params: FullParams, username: Annotated[str, Depends(get_current_username)]
        ):

            try:
                result = Result(**task(**params.model_dump()))
                state = TaskState.success.value
            except Exception:
                result = traceback.format_exc()
                state = TaskState.failure.value

            return ResultInfo(result=result, state=state)

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
                    result=f"{result_id=} not exist, current {app.state.RUNNING_ID=}",
                )

            async_result = celery_app.AsyncResult(result_id)

            # 立即获取 状态以及数据 尽量避免不一致的情况
            state = async_result.state
            traceback = async_result.traceback
            result = async_result.result

            if state == TaskState.success.value:
                result = Result(**result)
            elif state == TaskState.failure.value:
                result = str(traceback)
            else:
                result = str(result)

            return ResultInfo(id=result_id, state=state, result=result)


for task_name in LOADED_TASKS:
    get_task_apis(task_name)
