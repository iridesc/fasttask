import json
import os
import sys
import uuid
import secrets
import traceback

import redis

from enum import Enum
from typing import Any, Union, Annotated
from importlib import import_module
from pydantic import BaseModel
from starlette.responses import FileResponse
from fastapi import Depends, FastAPI, HTTPException, status, UploadFile
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from celery_app import app as celery_app
from tools import (
    check_file_name,
    get_bool_env,
    get_safe_file_name,
    load_task_names,
)
from setting import project_title, project_description, project_summary, project_version

sys.path.append("tasks")

CONF_DIR = os.environ["CONF_DIR"]
redis_params = {
    "host": os.environ["MASTER_HOST"],
    "port": os.environ["TASK_QUEUE_PORT"],
    "password": os.environ["TASK_QUEUE_PASSWD"],
    "decode_responses": True,
}


def initialize_running_id():
    global RUNNING_ID
    with redis.StrictRedis(
        **redis_params,
        db=0,
    ) as r:
        persisted_running_id = r.get("fasttask:current_running_id")

        if persisted_running_id:
            RUNNING_ID = persisted_running_id
            print(f"Using persisted RUNNING_ID: {RUNNING_ID}")
        else:
            RUNNING_ID = str(uuid.uuid4())
            r.set("fasttask:current_running_id", RUNNING_ID)
            print(f"Generated new RUNNING_ID to redis: {RUNNING_ID}")


initialize_running_id()


class TaskState(Enum):
    pending = "PENDING"
    started = "STARTED"
    failure = "FAILURE"
    success = "SUCCESS"
    revoked = "REVOKED"
    retry = "RETRY"


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


def load_user_to_passwd() -> dict:
    auth_file = f"{CONF_DIR}/user_to_passwd.json"
    if not os.path.exists(auth_file):
        return dict()
    with open(auth_file, "r") as f:
        return json.load(f)


def get_current_username(
    credentials: Annotated[HTTPBasicCredentials, Depends(HTTPBasic())],
):
    user_to_passwd = load_user_to_passwd()
    if not user_to_passwd:
        return "Anonymous"

    if not (
        credentials.username in user_to_passwd
        and secrets.compare_digest(
            credentials.password.encode("utf8"),
            user_to_passwd[credentials.username].encode("utf8"),
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="UNAUTHORIZED",
            headers={"WWW-Authenticate": "Basic"},
        )

    return credentials.username


def try_import_Data(task_model, DataName) -> type:
    try:
        return getattr(task_model, DataName)
    except Exception as error:
        print(f"{task_model=} {DataName=} not found! {error=}")
        return Any


def load_redis_task_infos() -> dict:
    with redis.StrictRedis(
        **redis_params,
        db=1,
    ) as r:
        task_id_to_infos = dict()
        for i in range(r.llen("celery")):
            raw_info = json.loads(r.lindex("celery", i))
            task_id_to_infos[raw_info["headers"]["id"]] = {
                "task": raw_info["headers"]["task"],
                "status": TaskState.pending.value,
            }
    with redis.StrictRedis(
        **redis_params,
        db=2,
    ) as r:
        for key in r.scan_iter(match="*"):
            raw_info = json.loads(r.get(key))
            task_id_to_infos[raw_info["task_id"]] = {
                "task": raw_info["name"],
                "status": raw_info["status"],
            }
        return task_id_to_infos


doc_url = None
if get_bool_env("API_DOCS"):
    doc_url = "/docs"

redoc_url = None
if get_bool_env("API_REDOC"):
    redoc_url = "/redoc"


app = FastAPI(
    title=project_title,
    description=project_description,
    summary=project_summary,
    version=project_version,
    docs_url=doc_url,
    redoc_url=redoc_url,
)


if get_bool_env("API_STATUS_INFO"):

    @app.get("/status_info")
    def status_info(username: Annotated[str, Depends(get_current_username)]):
        task_to_statistics_info = dict()
        status_to_amount = dict()

        for task_info in load_redis_task_infos().values():
            status = task_info["status"]
            task = task_info["task"]
            status_to_amount.setdefault(status, 0)
            status_to_amount[status] += 1
            task_to_statistics_info.setdefault(task, dict()).setdefault(status, 0)
            task_to_statistics_info[task][status] += 1

        return {
            "username": username,
            "status_to_amount": status_to_amount,
            "task_to_amount_status_statics": task_to_statistics_info,
        }


if get_bool_env("API_FILE_DOWNLOAD"):

    @app.get("/download")
    def download(file_name, username: Annotated[str, Depends(get_current_username)]):
        validated_file_path = check_file_name(file_name, username)
        if not os.path.isfile(validated_file_path):
            return HTTPException(status_code=404, detail="File not found")
        display_filename = os.path.basename(validated_file_path)
        print(f"{username=}: 下载: {display_filename=} {validated_file_path=}")
        return FileResponse(validated_file_path, filename=display_filename)


if get_bool_env("API_FILE_UPLOAD"):

    @app.post("/upload")
    def upload(
        file: UploadFile, username: Annotated[str, Depends(get_current_username)]
    ):
        file_name = get_safe_file_name(file.filename, username)
        with open(
            os.path.join(
                "./files/",
                file_name,
            ),
            "wb",
        ) as f:
            for i in iter(lambda: file.file.read(1024 * 1024 * 10), b""):
                f.write(i)

        return {"file_name": file_name}


if get_bool_env("API_REVOKE"):

    @app.post(f"/revoke", response_model=ActionResp)
    def revoke(
        result_id_params: ResultIDParams,
        username: Annotated[str, Depends(get_current_username)],
    ):
        resp = ActionResp()
        result_id = result_id_params.result_id
        if not result_id.startswith(RUNNING_ID):
            resp.message = f"invalid {result_id=} current {RUNNING_ID=}"
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
    task = getattr(
        import_module(package="loaded_tasks", name=f".{task_name}"), f"_{task_name}"
    )

    task_model = import_module(package="tasks", name=f".{task_name}")
    Result = try_import_Data(task_model, "Result")
    Params = try_import_Data(task_model, "Params")

    class ResultInfo(BaseModel):
        id: str = ""
        state: TaskState = TaskState.failure.value
        result: Union[Result, str]

    if get_bool_env("API_RUN"):

        @app.post(f"/run/{task_name}", response_model=ResultInfo)
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

        @app.post(f"/create/{task_name}", response_model=ResultInfo)
        def create(
            params: Params, username: Annotated[str, Depends(get_current_username)]
        ):
            try:
                async_result = task.apply_async(
                    args=(),
                    kwargs=params.model_dump(),
                    task_id=f"{RUNNING_ID}-{uuid.uuid4()}",
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

        @app.get(f"/check/{task_name}", response_model=ResultInfo)
        def check(
            result_id: str, username: Annotated[str, Depends(get_current_username)]
        ):
            if not result_id.startswith(RUNNING_ID):
                return ResultInfo(
                    id=result_id,
                    state=TaskState.failure.value,
                    result=f"{result_id=} not exist, current {RUNNING_ID=}",
                )

            async_result = celery_app.AsyncResult(result_id)
            if async_result.state == TaskState.success.value:
                result = Result(**async_result.result)
            elif async_result.state == TaskState.failure.value:
                result = str(async_result.traceback)
            else:
                result = str(async_result.result)

            return ResultInfo(id=result_id, state=async_result.state, result=result)


for task_name in load_task_names("tasks"):
    get_task_apis(task_name)
