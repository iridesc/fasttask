import sys
from celery_app import app

sys.path.append("tasks")

from tasks.get_hypotenuse import get_hypotenuse

 
@app.task
def _get_hypotenuse(*args, **kwargs):
    return get_hypotenuse(*args, **kwargs)
