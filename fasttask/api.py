from typing import Any
from enum import Enum
from fastapi import FastAPI
from pydantic import BaseModel
from importlib import import_module
from celery_app import app as celery_app
from tools import load_task_names
import traceback


app = FastAPI()


class TaskState(Enum):
    pending = "PENDING"
    started = "STARTED"
    failure = "FAILURE"
    success = "SUCCESS"
    revoked = "REVOKED"
    retry = "RETRY"


def makeup_result_info(async_result: celery_app.AsyncResult, result_info):
    result_info.id = async_result.id
    # task_name=crate_task_info.name
    result_info.state = async_result.state
    if async_result.state == TaskState.success.value:
        result_info.result = async_result.result
    elif async_result.state == TaskState.failure.value:
        result_info.result = str(async_result.result)
    return result_info


class EmptyInfo(BaseModel):
    ...


def try_import_Data(task_model, DataName):
    try:
        return getattr(task_model, DataName)
    except Exception as e:
        print(e)
        print(f"{task_model} {DataName} not found!")
        return Any


task_names = load_task_names()
for task_name in task_names:
    print("importing ", task_name)
    task_model = import_module(package="tasks", name=f".{task_name}")

    task = getattr(task_model, task_name)

    Result = try_import_Data(task_model, "Result")
    Params = try_import_Data(task_model, "Params")

    class ResultInfo(BaseModel):
        id: str = ""
        # task_name: str
        state: TaskState = TaskState.failure.value
        result: Result

    @app.post(f"/run/{task_name}/", response_model=Result)
    def run(params: Params):
        return task(**(params.model_dump() if isinstance(params, BaseModel) else params))

    @app.post(f"/create/{task_name}/", response_model=ResultInfo)
    def create(params: Params):
        result_info = ResultInfo()
        try:
            async_result = task.delay(**(params.model_dump() if isinstance(params, BaseModel) else params))
        except Exception:
            result_info.result = traceback.format_exc()
        else:
            makeup_result_info(async_result, result_info)
        return result_info

    @app.get(f"/check/{task_name}", response_model=ResultInfo)
    def check(result_id: str):
        return makeup_result_info(celery_app.AsyncResult(result_id), ResultInfo())

    globals_dict = globals()
    globals_dict[f"run_{task_name}"] = run
    globals_dict[f"create_{task_name}"] = create
    globals_dict[f"check_{task_name}"] = check


@app.get("/")
def home():
    return task_names
