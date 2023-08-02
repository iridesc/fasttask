from ..celery import app
import time


@app.task
def add(x, y):
    time.sleep(3)
    return x + y
