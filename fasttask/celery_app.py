from celery import Celery
from tools import load_task_names


app = Celery(
    'celery_task',
    broker='redis://127.0.0.1:6379/1',
    backend='redis://127.0.0.1:6379/2',
    include=[
        f'loaded_tasks.{task_name}' for task_name in load_task_names("loaded_tasks")
    ],
    result_extended=True
)

app.config_from_object("setting", namespace="celery")


if __name__ == '__main__':
    app.start()
