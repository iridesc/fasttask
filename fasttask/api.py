from typing import Any, List
from enum import Enum
from fastapi import FastAPI
from pydantic import BaseModel
from importlib import import_module
from celery_task.celery import app as celery_app
import traceback

app = FastAPI()


class CreateTaskInfo(BaseModel):
    name: str = "add"
    args: List[Any] = [1, 1]
    kwargs: dict = dict()


class TaskState(Enum):
    pending = "PENDING"
    started = "STARTED"
    failure = "FAILURE"
    success = "SUCCESS"
    revoked = "REVOKED"
    retry = "RETRY"


class ResultInfo(BaseModel):
    id: str = ""
    # task_name: str
    state: TaskState = TaskState.failure.value
    result: Any = None


def makeup_result_info(result: celery_app.AsyncResult, result_info: ResultInfo = None):
    result_info = result_info if result_info else ResultInfo()
    result_info.id = result.id
    # task_name=crate_task_info.name
    result_info.state = result.state
    if result.state == TaskState.success.value:
        result_info.result = result.result
    elif result.state == TaskState.failure.value:
        result_info.result = str(result.result)
    return result_info


@app.post("/create/", response_model=ResultInfo)
def create(crate_task_info: CreateTaskInfo, ):

    result_info = ResultInfo()
    try:
        module = import_module(package="celery_task.tasks", name=f".{crate_task_info.name}")
        task = getattr(module, crate_task_info.name)
        result = task.delay(*crate_task_info.args, **crate_task_info.kwargs)
    except Exception:
        result_info.result = traceback.format_exc()
    else:
        makeup_result_info(result, result_info)
    return result_info


@app.get("/check/", response_model=ResultInfo)
def check(result_id: str):
    return makeup_result_info(celery_app.AsyncResult(result_id))
