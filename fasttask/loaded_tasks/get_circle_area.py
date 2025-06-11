import sys
from celery_app import app

sys.path.append("tasks")

from tasks.get_circle_area import get_circle_area


@app.task
def _get_circle_area(*args, **kwargs):
    return get_circle_area(*args, **kwargs)
