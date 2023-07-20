from ..celery import app
import time


@app.task
def add(x, y):
    time.sleep(50)
    return x + y
