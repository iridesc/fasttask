import os
from celery import Celery
from kombu import Queue, Exchange
from tools import load_task_names, get_int_env

master_host = os.environ["master_host"]
task_queue_port = os.environ["task_queue_port"]
task_queue_passwd = os.environ["task_queue_passwd"]
result_expires = get_int_env("result_expires", default=24 * 3600)
worker_pool = os.environ["worker_pool"]
worker_concurrency = get_int_env("worker_concurrency", default=4)

enabled_task_names = load_task_names("loaded_tasks")

app = Celery(
    'fasttask',
    broker=f'redis://:{task_queue_passwd}@{master_host}:{task_queue_port}/1',
    backend=f'redis://:{task_queue_passwd}@{master_host}:{task_queue_port}/2',
    include=[
        f'loaded_tasks.{task_name}' for task_name in enabled_task_names
    ],
    result_extended=True,
    task_track_started=True
)

app.conf.update({
    'QUEUES': tuple(
        Queue(task_name, Exchange('tasks_exchange'),
              routing_key=task_name) for task_name in enabled_task_names
    ),
    'DEFAULT_EXCHANGE': 'tasks_exchange',
    'DEFAULT_EXCHANGE_TYPE': 'direct',
    'ROUTES': {
        f'loaded_tasks.{task_name}._{task_name}': {
            'queue': task_name,
            'routing_key': task_name,
        } for task_name in enabled_task_names
    },
    'result_expires': result_expires,
    'worker_pool': worker_pool,
    'worker_concurrency': worker_concurrency,
    'broker_connection_retry_on_startup': True,
})

if __name__ == '__main__':
    app.start()
