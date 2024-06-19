import os
from celery import Celery
from tools import load_task_names

master_host = os.environ["master_host"]
task_queue_port = os.environ["task_queue_port"]
task_queue_passwd = os.environ["task_queue_passwd"]

app = Celery(
    'fasttask',
    broker=f'redis://:{task_queue_passwd}@{master_host}:{task_queue_port}/1',
    backend=f'redis://:{task_queue_passwd}@{master_host}:{task_queue_port}/2',
    include=[
        f'loaded_tasks.{task_name}' for task_name in load_task_names("loaded_tasks")
    ],
    result_extended=True,
    task_track_started=True
)

app.config_from_object("setting", namespace="celery")


if __name__ == '__main__':
    app.start()
