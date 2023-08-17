from celery import Celery
from tools import load_task_names
app = Celery(
    'celery_task',
    broker='redis://redis:6379/1',
    backend='redis://redis:6379/2',
    include=[
        f'tasks.{task_name}' for task_name in load_task_names()
    ],
    result_extended=True
)

app.config_from_object("setting")


if __name__ == '__main__':
    app.start()
