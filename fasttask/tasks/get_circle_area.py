from math import pi
from typing import Union
from pydantic import BaseModel

from packages.tools import sleep_random


class Params(BaseModel):
    r: Union[float, int]


class Result(BaseModel):
    area: Union[float, int]


def get_circle_area(r):
    if r <= 0:
        raise ValueError("r must > 0")
    print("running...")
    sleep_random()
    result = Result(area=pi * r**2)

    return result.model_dump()


if __name__ == '__main__':
    print(get_circle_area(1))
