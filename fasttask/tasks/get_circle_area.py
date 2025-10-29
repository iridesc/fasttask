from math import pi
from typing import Union
from pydantic import BaseModel

from packages.tools import sleep_random, cache_result


class Params(BaseModel):
    r: Union[float, int]


class Result(BaseModel):
    area: Union[float, int]


# @cache_result(ttl=60 * 10)
def get_circle_area(r):
    if r <= 0:
        raise ValueError("r must > 0")
    print("running...")
    sleep_random()
    return {"area": pi * r**2}


if __name__ == '__main__':
    print(get_circle_area(1))
