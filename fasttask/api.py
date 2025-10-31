import contextlib
import datetime
import os
import sys
import uuid
import traceback
import asyncio
from enum import Enum
from typing import Any, Union, Annotated, Optional
from importlib import import_module

import redis

from utils.tools import get_list_env, get_bool_env
from pydantic import BaseModel, Field
from starlette.responses import FileResponse
from fastapi import Depends, FastAPI, HTTPException, UploadFile
from celery_app import app as celery_app
from utils.api_utils import (
    TaskState,
    check_file_name,
    get_current_username,
    get_task_statistics_info,
    get_worker_status,
    initialize_running_id,
    load_redis_task_infos,
    try_import_Data,
    upload_sync, redis_params,
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
    docs_url="/docs" if get_bool_env("API_DOCS") else None,
    redoc_url="/redoc" if get_bool_env("API_REDOC") else None,
    lifespan=lifespan,
)


if get_bool_env("API_STATUS_INFO"):

    @app.get("/status_info", tags=["Monitoring"])
    async def status_info(username: Annotated[str, Depends(get_current_username)]):
        worker_status, task_infos = await asyncio.gather(
            get_worker_status(celery_app), load_redis_task_infos(LOADED_TASKS)
        )

        task_infos = task_infos.values()
        end_time = datetime.datetime.now(datetime.timezone.utc)

        info = {
            "running_id": app.state.RUNNING_ID,
            "username": username,
            "worker_status": worker_status,
            "task_info_total": get_task_statistics_info(
                end_time=end_time, task_infos=task_infos
            ),
        }

        for task_name in LOADED_TASKS:
            info[f"task_info_{task_name}"] = get_task_statistics_info(
                end_time=end_time, task_infos=task_infos, task_name=task_name
            )

        return info


    @app.get("/pending_task_count")
    def get_pending_task_count():
        pending_counts = {}
        with redis.StrictRedis(
                **redis_params,
                db=1,
        ) as r:
            for task_name in TASK_NAMES:
                try:
                    pending_counts[task_name] = int(r.llen(task_name))
                except Exception:
                    # 单个队列统计失败不阻断整体，记录并置 0
                    pending_counts[task_name] = 0
        return pending_counts


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

    class ConcurrencyParams(BaseModel):
        concurrency_key: str = Field(..., description='并发控制的key')
        max_concurrency: int = Field(..., description='最大并发量')
        # countdown: int = Field(default=60, description='退避时间（秒）')
        expire: int = Field(default=30 * 60, description='锁的过期时间（秒）避免死锁')

    class ResultInfo(BaseModel):
        id: str = ""
        state: TaskState = TaskState.failure.value
        result: Union[Result, str]

    if get_bool_env("API_RUN"):

        @app.post(f"/run/{task_name}", response_model=ResultInfo, tags=task_base_tag)
        def run(
            params: Params, username: Annotated[str, Depends(get_current_username)]
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
            params: Params, username: Annotated[str, Depends(get_current_username)],
            concurrency_params: Optional[ConcurrencyParams] = None,
        ):
            try:
                task_params = params.model_dump()
                task_params["concurrency_params"] = concurrency_params
                async_result = task.apply_async(
                    args=(),
                    kwargs=task_params,
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
            if async_result.state == TaskState.success.value:
                result = Result(**async_result.result)
            elif async_result.state == TaskState.failure.value:
                result = str(async_result.traceback)
            else:
                result = str(async_result.result)

            return ResultInfo(id=result_id, state=async_result.state, result=result)


for task_name in LOADED_TASKS:
    get_task_apis(task_name)
