from celery_app import app
from tasks.packages.tools import xx


@app.task
def add_and_xx(a, b):
    return xx((a + b), b)
