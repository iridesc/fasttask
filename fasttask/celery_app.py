import os
from celery import Celery
from utils.tools import get_bool_env, get_list_env
from kombu import Queue, Exchange

MASTER_HOST = os.environ["MASTER_HOST"]
TASK_QUEUE_PORT = os.environ["TASK_QUEUE_PORT"]
TASK_QUEUE_PASSWD = os.environ["TASK_QUEUE_PASSWD"]
FLOWER_ENABLED = get_bool_env("FLOWER_ENABLED")


loaded_tasks = get_list_env("LOADED_TASKS")

app = Celery(
    "fasttask",
    broker=f"redis://:{TASK_QUEUE_PASSWD}@{MASTER_HOST}:{TASK_QUEUE_PORT}/1",
    backend=f"redis://:{TASK_QUEUE_PASSWD}@{MASTER_HOST}:{TASK_QUEUE_PORT}/2",
    include=[f"loaded_tasks.{task}" for task in loaded_tasks],
    result_extended=True,
    task_track_started=True,
)

app.conf.update(
    {
        # 核心：这行代码等同于命令行的 -E 参数
        "worker_send_task_events": FLOWER_ENABLED,

        "task_queues": tuple(
            Queue(task, Exchange("tasks_exchange"), routing_key=task)
            for task in loaded_tasks
        ),
        "task_default_exchange": "tasks_exchange",
        "task_default_exchange_type": "direct",
        "task_routes": {
            f"loaded_tasks.{task}._{task}": {
                "queue": task,
                "routing_key": task,
            }
            for task in loaded_tasks
        },
        "result_expires": int(os.environ["RESULT_EXPIRES"]),
        "worker_pool": os.environ["WORKER_POOL"],
        "worker_concurrency": int(os.environ["WORKER_CONCURRENCY"]),
        "worker_max_tasks_per_child": 100,  # 防止内存泄漏
        "broker_connection_retry_on_startup": True,
        "broker_connection_max_retries": None,
        "broker_pool_limit": 10,
        "worker_state_db": os.path.join(os.environ["CELERY_DIR"], "worker.state"),
        "worker_loglevel": "INFO",
        "task_acks_late": True,
        "task_publish_retry": True,
        "task_serializer": "json",
        "result_persistent": True,
        "result_serializer": "json",
        "accept_content": ["json"],
        "worker_prefetch_multiplier": 1,
        "task_reject_on_worker_lost": True,
        # 子进程「疑似丢失」（OOM / SIGKILL / 崩溃）时的判定宽限期：billiard 检测到 worker 失联后
        # 会先等这么久（billiard/pool.py: "we have no way to accurately tell if it did"），
        # 确认它真回不来了再把任务标记为 WorkerLostError。与上一行是一对：
        # 前者决定「多久判定丢失」，后者决定「判定后把消息放回队列而不是 ack 丢弃」。
        # 注意异常类型不是 WorkerLostError 时这条链路不会走到（例如 billiard >= 4.3.0 会把
        # 子进程的 SystemExit 当作任务结果上报，见 requirements.txt）。
        "worker_lost_wait": 10.0,
        "broker_transport_options": {
            "visibility_timeout": int(os.environ["VISIBILITY_TIMEOUT"]),
            # Redis 连接建立超时 (秒)
            "socket_connect_timeout": 5,
            # Redis 读写操作超时及空闲连接清理超时 (秒)
            "socket_timeout": 60,
            # Redis 连接健康检查间隔 (秒)
            "health_check_interval": 20,
            "socket_keepalive": True,
            "retry_on_timeout": True,
        },
        "result_backend_transport_options": {
            "socket_connect_timeout": 5,
            "socket_timeout": 60,
            "health_check_interval": 20,
            "socket_keepalive": True,
            "retry_policy": {
                "max_retries": 10,
                "interval_start": 0.2,
                "interval_step": 0.5,
                "interval_max": 6.0,
            },
        },
    }
)

if __name__ == "__main__":
    app.start()
