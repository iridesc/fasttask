from typing import Union
from pydantic import BaseModel

from packages.tools import xx, sleep_random


class Params(BaseModel):
    a: Union[float, int]
    b: Union[float, int]


class Result(BaseModel):
    hypotenuse: Union[float, int]


def get_hypotenuse(a, b):
    if a <= 0 or b <= 0:
        raise ValueError("side length must > 0")
    print("running...")
    sleep_random()
    result = Result(hypotenuse=(xx(a) + xx(b))**0.5)
    return result.model_dump()


if __name__ == '__main__':
    with open("files/a.txt", "w") as f:
        f.write("a")
    print(get_hypotenuse(3, 4))
