from typing import Dict
from enum import Enum
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()


class CreateTaskInfo(BaseModel):
    data: Dict


class TaskStatus(Enum):
    waiting = "waiting"
    running = "running"
    error = "error"
    done = "done"


class TaskInfo(BaseModel):
    task_type: str = ""
    id: str = ""
    status: TaskStatus = TaskStatus.waiting
    result: dict = dict()
    message: str


@app.post("/create/", response_model=TaskInfo)
def create_task(crate_task_info: CreateTaskInfo):
    return TaskInfo


@app.get("/check/", response_model=TaskInfo)
def check_task(task_id: str):
    return TaskInfo
