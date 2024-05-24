import os
from celery import Celery
from tools import load_task_names

master_host = os.environ["master_host"]
master_port = os.environ["master_port"]
master_passwd = os.environ["master_passwd"]

app = Celery(
    'celery_task',
    broker=f'redis://{master_passwd}@{master_host}:{master_port}/1',
    backend=f'redis://{master_passwd}@{master_host}:{master_port}/2',
    include=[
        f'loaded_tasks.{task_name}' for task_name in load_task_names("loaded_tasks")
    ],
    result_extended=True
)

app.config_from_object("setting", namespace="celery")


if __name__ == '__main__':
    app.start()
