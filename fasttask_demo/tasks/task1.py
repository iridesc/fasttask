from ..celery import app
import time
import random

@app.task
def task1():    
    time.sleep(time.sleep(random.random()))
    return "this is task1 result"
