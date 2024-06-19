import json
import os
import sys
import uuid
import secrets
import traceback

import uvicorn
import redis

from enum import Enum
from typing import Any, Union, Annotated
from importlib import import_module
from pydantic import BaseModel
from starlette.responses import FileResponse
from fastapi import Depends, FastAPI, HTTPException, status, UploadFile
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from celery_app import app as celery_app
from tools import load_task_names
from setting import project_title, project_description, project_summary, project_version, api_docs, api_redoc

import setting


sys.path.append("tasks")

running_id = str(uuid.uuid4())
app = FastAPI(
    title=project_title,
    description=project_description,
    summary=project_summary,
    version=project_version,
    docs_url="/docs" if api_docs else None,
    redoc_url="/redoc" if api_redoc else None,
)
security = HTTPBasic()


if not setting.user_to_passwd:
    print("setting.user_to_passwd not set! anyone can access this service!")


class TaskState(Enum):
    pending = "PENDING"
    started = "STARTED"
    failure = "FAILURE"
    success = "SUCCESS"
    revoked = "REVOKED"
    retry = "RETRY"


class DownloadFileInfo(BaseModel):
    file_name: str = "lp.jpg"


def get_current_username(credentials: Annotated[HTTPBasicCredentials, Depends(security)]):
    if not setting.user_to_passwd:
        return "Anyone"

    if not (credentials.username in setting.user_to_passwd and secrets.compare_digest(
        credentials.password.encode("utf8"), setting.user_to_passwd[credentials.username].encode("utf8")
    )):

        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="UNAUTHORIZED",
            headers={"WWW-Authenticate": "Basic"},
        )

    return credentials.username


def try_import_Data(task_model, DataName):
    try:
        return getattr(task_model, DataName)
    except Exception as error:
        print(f"{task_model=} {DataName=} not found! {error=}")
        return Any


@app.get("/status_info")
def status_info(username: Annotated[str, Depends(get_current_username)]):

    task_to_statistics_info = dict()
    status_to_amount = dict()

    for db in ("1", "2"):
        r = redis.Redis(
            host=os.environ["master_host"],
            port=os.environ["task_queue_port"],
            password=os.environ["task_queue_passwd"],
            db=db,
            decode_responses=True
        )

        for key in r.scan_iter(match="*"):

            task_info = json.loads(r.get(key))

            status = task_info.get("status", TaskState.pending)
            name = task_info["name"]

            status_to_amount.setdefault(status, 0)
            status_to_amount[status] += 1
            task_to_statistics_info.setdefault(name, dict()).setdefault(status, 0)
            task_to_statistics_info[name][status] += 1

    return {
        "username": username,
        "status_to_amount": status_to_amount,
        "task_to_amount_status_statics": task_to_statistics_info
    }


def makeup_api(task_name):
    print("importing ", task_name)

    task = getattr(import_module(package="loaded_tasks", name=f".{task_name}"), f"_{task_name}")

    task_model = import_module(package="tasks", name=f".{task_name}")
    Result = try_import_Data(task_model, "Result")
    Params = try_import_Data(task_model, "Params")

    class ResultInfo(BaseModel):
        id: str = ""
        state: TaskState = TaskState.failure.value
        result: Union[Result, str]

    @app.post(f"/run/{task_name}", response_model=ResultInfo)
    def run(params: Params, username: Annotated[str, Depends(get_current_username)]):

        print(f"api run: {username=}")

        try:
            result = Result(**task(**params.model_dump()))
            state = TaskState.success.value
        except Exception:
            result = traceback.format_exc()
            state = TaskState.failure.value

        return ResultInfo(result=result, state=state)

    @app.post(f"/create/{task_name}", response_model=ResultInfo)
    def create(params: Params, username: Annotated[str, Depends(get_current_username)]):

        print(f"api create: {username=}")

        try:
            async_result = task.apply_async(args=(), kwargs=params.model_dump(), task_id=f"{running_id}-{uuid.uuid4()}")
        except Exception:

            result_info = ResultInfo(result=traceback.format_exc())
        else:
            result_info = ResultInfo(id=async_result.id, state=async_result.state, result="")

        return result_info

    @app.get(f"/check/{task_name}", response_model=ResultInfo)
    def check(result_id: str, username: Annotated[str, Depends(get_current_username)]):

        print(f"api check: {username=}")

        if not result_id.startswith(running_id):
            return ResultInfo(
                id=result_id,
                state=TaskState.failure,
                result=f"{result_id=} not exist, current {running_id=}"
            )

        async_result = celery_app.AsyncResult(result_id)
        if async_result.state == TaskState.success.value:
            result = Result(**async_result.result)
        elif async_result.state == TaskState.failure:
            result = str(async_result.traceback)
        else:
            result = str(async_result.result)

        return ResultInfo(
            id=result_id,
            state=async_result.state,
            result=result
        )

    return run, create, check


def get_download_api():
    @app.get("/download")
    def download(file_name, username: Annotated[str, Depends(get_current_username)]):
        if ".." in file_name:
            raise Exception(f"{username=}: .. is not allowed in path")
        file_path = os.path.join("./files/", file_name)
        file_path = os.path.abspath(file_path)
        filename = os.path.basename(file_path)
        print(f"{username=}: download: {filename=} {file_path=}:")
        return FileResponse(file_path, filename=filename)
    return download


def get_upload_api():
    @app.post("/upload")
    def upload(file: UploadFile, username: Annotated[str, Depends(get_current_username)]):
        if ".." in file.filename:
            raise Exception(f"{username=}: .. is not allowed in {file.filename=}")

        filename = f"upload_{uuid.uuid4()}_{file.filename}"
        with open(os.path.join("./files/", filename), 'wb') as f:
            for i in iter(lambda: file.file.read(1024 * 1024 * 10), b''):
                f.write(i)

        return {"file_name": filename}

    return upload


globals_dict = globals()
for task_name in load_task_names("tasks"):
    globals_dict[f"run_{task_name}"], globals_dict[f"create_{task_name}"], globals_dict[f"check_{task_name}"] = makeup_api(
        task_name)

if setting.file_download:
    globals_dict["download"] = get_download_api()

if setting.file_upload:
    globals_dict["upload"] = get_upload_api()


if __name__ == '__main__':
    uvicorn.run('api:app', host='0.0.0.0', port=8005, reload=True)
