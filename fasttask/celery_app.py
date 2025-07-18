import os
from celery import Celery
from kombu import Queue, Exchange
from tools import load_task_names

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
        "result_expires": int(os.environ["RESULT_EXPIRES"]),
        "worker_pool": os.environ["WORKER_POOL"],
        "worker_concurrency": int(os.environ["WORKER_CONCURRENCY"]),
        "broker_connection_retry_on_startup": True,
        "broker_connection_max_retries": None,
        "worker_state_db": os.path.join(os.environ["CELERY_DIR"], "worker.state"),
        "worker_loglevel": "INFO",
        "task_acks_late": True,
        "worker_prefetch_multiplier": 1,
        "task_reject_on_worker_lost": True,
        "worker_terminate_timeout": 5,
        "broker_transport_options": {
            "visibility_timeout": int(os.environ["VISIBILITY_TIMEOUT"]),
            # Redis 连接建立超时 (秒)
            "socket_connect_timeout": 5,
            # Redis 读写操作超时及空闲连接清理超时 (秒)
            "socket_timeout": 60,
            # Redis 连接健康检查间隔 (秒)
            "health_check_interval": 60,
        },
        "result_backend_transport_options": {
            "socket_connect_timeout": 5,
            "socket_timeout": 60,
            "health_check_interval": 60,
        },
    }
)

if __name__ == "__main__":
    app.start()
