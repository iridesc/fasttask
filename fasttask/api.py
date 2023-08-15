import os
import traceback
from typing import Any, Union
from enum import Enum
from fastapi import FastAPI
from pydantic import BaseModel
from importlib import import_module
from celery_app import app as celery_app
from tools import load_task_names
from starlette.responses import FileResponse


app = FastAPI()


class TaskState(Enum):
    pending = "PENDING"
    started = "STARTED"
    failure = "FAILURE"
    success = "SUCCESS"
    revoked = "REVOKED"
    retry = "RETRY"

class DownloadFileInfo(BaseModel):
    file_path:str

def try_import_Data(task_model, DataName):
    try:
        return getattr(task_model, DataName)
    except Exception as e:
        print(e)
        print(f"{task_model} {DataName} not found!")
        return Any


def makeup_api(task_name):
    print("importing ", task_name)
    task_model = import_module(package="tasks", name=f".{task_name}")

    task = getattr(task_model, task_name)

    Result = try_import_Data(task_model, "Result")
    Params = try_import_Data(task_model, "Params")

    class ResultInfo(BaseModel):
        id: str = ""
        state: TaskState = TaskState.failure.value
        result: Union[Result, str]

    def makeup_result_info(async_result: celery_app.AsyncResult, result_info):
        print("async_result", async_result)
        print("async_result.state", async_result.state)

        print(result_info)

        return result_info

    @app.post(f"/run/{task_name}/", response_model=ResultInfo)
    def run(params: Params):
        try:
            result = Result(**task(**params.model_dump()))
            state = TaskState.success.value
        except Exception:
            result = traceback.format_exc()
            state = TaskState.failure.value

        return ResultInfo(result=result, state=state)

    @app.post(f"/create/{task_name}/", response_model=ResultInfo)
    def create(params: Params):

        try:
            async_result = task.delay(**params.model_dump())

        except Exception:

            result_info = ResultInfo(result=traceback.format_exc())
        else:
            result_info = ResultInfo(id=async_result.id, state=async_result.state, result="")

        return result_info

    @app.get(f"/check/{task_name}", response_model=ResultInfo)
    def check(result_id: str):
        async_result = celery_app.AsyncResult(result_id)
        return ResultInfo(
            id=result_id,
            state=async_result.state,
            result=Result(**async_result.result) if async_result.state == TaskState.success.value else str(
                async_result.result)
        )

    return run, create, check


@app.get("/download")
def download_file(file_path):
    file_path = os.path.abspath(file_path)

    if ".." in file_path.split("/"):
        raise Exception(".. is not allowed in path")
    
    return FileResponse("file_path", filename="user.xlsx")


globals_dict = globals()
for task_name in load_task_names():
    globals_dict[f"run_{task_name}"], globals_dict[f"create_{task_name}"], globals_dict[f"check_{task_name}"] = makeup_api(
        task_name)
