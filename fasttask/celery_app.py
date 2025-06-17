import os
from celery import Celery
from kombu import Queue, Exchange
from tools import load_task_names, get_int_env

MASTER_HOST = os.environ["MASTER_HOST"]
TASK_QUEUE_PORT = os.environ["TASK_QUEUE_PORT"]
TASK_QUEUE_PASSWD = os.environ["TASK_QUEUE_PASSWD"]

enabled_task_names = load_task_names("loaded_tasks")

app = Celery(
    "fasttask",
    broker=f"redis://:{TASK_QUEUE_PASSWD}@{MASTER_HOST}:{TASK_QUEUE_PORT}/1",
    backend=f"redis://:{TASK_QUEUE_PASSWD}@{MASTER_HOST}:{TASK_QUEUE_PORT}/2",
    include=[f"loaded_tasks.{task_name}" for task_name in enabled_task_names],
    result_extended=True,
    task_track_started=True,
)

app.conf.update(
    {
        "task_queues": tuple(
            Queue(task_name, Exchange("tasks_exchange"), routing_key=task_name)
            for task_name in enabled_task_names
        ),
        "task_default_exchange": "tasks_exchange",
        "task_default_exchange_type": "direct",
        "task_routes": {
            f"loaded_tasks.{task_name}._{task_name}": {
                "queue": task_name,
                "routing_key": task_name,
            }
            for task_name in enabled_task_names
        },
        "result_expires": get_int_env("RESULT_EXPIRES", default=24 * 3600),
        "worker_pool": os.environ["WORKER_POOL"],
        "worker_concurrency": get_int_env("WORKER_CONCURRENCY", default=4),
        "broker_connection_retry_on_startup": True,
        "worker_state_db": os.path.join(os.environ["CELERY_DIR"], "worker.state"),
        "worker_loglevel": "INFO",
        "task_acks_late": True,
        "worker_prefetch_multiplier": 1,
        "task_reject_on_worker_lost": True,
        "broker_transport_options": {
            "visibility_timeout": int(os.environ["VISIBILITY_TIMEOUT"])
        },
    }
)

if __name__ == "__main__":
    app.start()
