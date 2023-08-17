from typing import Union
from pydantic import BaseModel
from celery.utils.log import get_task_logger

from celery_app import app
from tasks.packages.tools import xx, sleep_random


logger = get_task_logger(__name__)


class Params(BaseModel):
    a: Union[float, int]
    b: Union[float, int]


class Result(BaseModel):
    hypotenuse: Union[float, int]


@app.task
def get_hypotenuse(a, b):
    if a <= 0 or b <= 0:
        raise ValueError("side length must > 0")
    print("running...")
    sleep_random()
    result = Result(hypotenuse=(xx(a) + xx(b))**0.5)
    return result.model_dump()
