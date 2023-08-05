from celery_app import app
from tasks.packages.tools import xx

from pydantic import BaseModel


class Params(BaseModel):
    a: int
    b: int

class Result(BaseModel):
    


@app.task
def add_and_xx(a, b):
    return xx((a + b), b)
