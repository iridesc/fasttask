from typing import Dict, Any, List
from enum import Enum
from fastapi import FastAPI
from pydantic import BaseModel
from importlib import import_module, reload
from celery_task.celery import app as celery_app


app = FastAPI()


class CreateTaskInfo(BaseModel):
    name: str="add"
    args: List[Any]=[1,1] 
    kwargs: dict=dict()


class TaskState(Enum):
    pending = "PENDING"
    started = "STARTED"
    failure = "FAILURE"
    success = "SUCCESS"
    revoked = "REVOKED"
    retry = "RETRY"


class ResultInfo(BaseModel):
    id: str
    # task_name: str
    state: TaskState
    message: str = ""
    result: Any = None


@app.post("/create/", response_model=ResultInfo)
def create(crate_task_info: CreateTaskInfo, ):
    module = import_module(package="celery_task.tasks", name=f".{crate_task_info.name}")
    task = getattr(module, crate_task_info.name)
    result = task.delay(*crate_task_info.args, **crate_task_info.kwargs)

    result_info = ResultInfo(
        id=result.id,
        # task_name=crate_task_info.name,
        state=result.state
    )

    if result.successful():
        result_info.result = result.get()
    elif result.state == "FAILURE":
        result_info.message = result.get(propagate=False)

    return result_info


@app.get("/check/", response_model=ResultInfo)
def check(result_id: str):
    # print("init result")
    result = celery_app.AsyncResult(result_id)
    # print("init result info")
    result_info = ResultInfo(
        id=result.id,
        # task_name=result.task_name,
        state=result.state
    )
    # print("set result")

    if result.state == TaskState.success.value:
        # print("success")
        result_info.result = result.get()
    elif result.state == TaskState.failure.value:
        # print("failure")
        result_info.message = result.get()
        
    # print("return")

    return result_info
