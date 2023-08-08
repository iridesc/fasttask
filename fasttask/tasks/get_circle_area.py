from celery_app import app
from pydantic import BaseModel

from tasks.packages.tools import sleep_random
from typing import Union
from math import pi


class Params(BaseModel):
    r: Union[float, int]


class Result(BaseModel):
    area: Union[float, int]


@app.task
def get_circle_area(r):
    if r <= 0:
        raise ValueError("r must > 0")
    print("running...")
    sleep_random()
    result = Result(area=pi * r**2)

    return result.model_dump()
