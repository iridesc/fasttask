from logging import getLogger
import requests
from requests.auth import HTTPBasicAuth

TASK_STATUS_AUTH_USER = "admin"
TASK_STATUS_AUTH_PASSWD = "passwd"
TASK_FLOWER_URL = "https://localhost:9001/flower"


def _req(path, method="GET", params=None, json=None):
    auth = HTTPBasicAuth(TASK_STATUS_AUTH_USER, TASK_STATUS_AUTH_PASSWD)
    resp = requests.request(
        method=method,
        url=f"{TASK_FLOWER_URL}{path}",
        auth=auth,
        headers={"accept": "application/json"},
        params=params,
        json=json,
        timeout=30,
        verify=False,
    )
    resp.raise_for_status()
    return resp.json()


def get_queue_lengths():
    """
    获取所有队列的待处理任务数量

    Returns:
        dict: 队列名称到消息数的映射，如 {"celery": 10, "celery@worker1": 5}
    """
    data = _req("/api/queues/length", method="GET")
    queues = data.get("active_queues", [])
    return {q.get("name"): q.get("messages", 0) for q in queues}



def lock_empty_worker(worker_tag, logger):
    """
    锁定并检查空闲 worker，准备释放

    流程：
    1. 检查主机是否有正在运行的任务
    2. 有任务 -> 返回 False 不释放
    3. 无任务 -> 将并发数收缩到 0
    4. 再次确认是否有任务
    5. 有任务 -> 恢复并发数，返回 False 不释放
    6. 无任务 -> 返回 True，允许释放

    Args:
        worker_tag: Worker 标签

    Returns:
        bool: True 表示可以释放，False 表示不能释放
    """

    try:
        # 1. 获取 worker 状态
        workers = _req("/api/workers", method="GET", params={"refresh": 1})
        for worker_name, worker_info in workers.items():
            if worker_name.startswith(f"{worker_tag}@"):
                break
        else:
            logger.warning(
                f"Worker 对应的主机 {worker_tag=} 不在 Flower 中，可能worker未启动或者异常，允许释放"
            )
            return True

        # 检查活跃任务数
        if len(worker_info["active"]) + len(worker_info["reserved"]) > 0:
            logger.info(
                f"Worker {worker_name} 有 {worker_info['active']} 个活跃任务，不释放"
            )
            return False

        # 2. 没有任务，收缩并发到 0
        current_processes = len(worker_info["stats"]["pool"]["processes"])
        if current_processes > 0:
            _req(
                f"/api/worker/pool/shrink/{worker_name}",
                method="POST",
                params={"n": current_processes},
            )
            logger.info(f"Worker {worker_name} 并发已收缩到 0")

        # 3. 再次确认是否有任务
        workers = _req("/api/workers", method="GET", params={"refresh": 1})
        worker_info = workers[worker_name]
        if len(worker_info["active"]) + len(worker_info["reserved"]) > 0:
            # 4. 有任务，恢复并发数
            _req(
                f"/api/worker/pool/grow/{worker_name}",
                method="POST",
                params={"n": current_processes},
            )
            logger.info(
                f"Worker {worker_name} 有新任务，已恢复并发到 {current_processes}"
            )
            return False

        # 5. 无任务，允许释放
        logger.info(f"Worker {worker_name} 无活跃任务，允许释放")
        return True

    except requests.exceptions.RequestException as e:
        logger.error(f"检查 Worker {worker_name} 任务状态失败: {str(e)}")
        logger.exception(e)
        return False


if __name__ == "__main__":
    # logger = getLogger("test")
    # lock_empty_worker("get_hypotenuse", logger=logger)

    l = get_queue_lengths()
    print(l)
