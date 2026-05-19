"""
Flower API 测试脚本

测试 Flower 相关接口的功能和性能
文档参考: https://flower.readthedocs.io/
"""
import json
import os
import time
import requests
from requests.auth import HTTPBasicAuth
from urllib3.exceptions import InsecureRequestWarning

# 禁用 SSL 警告（测试环境使用自签名证书）
requests.packages.urllib3.disable_warnings(InsecureRequestWarning)


CONF_DIR = os.environ.get("CONF_DIR", "samples/files/fasttask/conf")


def setup_auth_file(user: str = "admin", passwd: str = "passwd"):
    """创建认证文件"""
    auth_file = os.path.join(CONF_DIR, "user_to_passwd.json")
    os.makedirs(CONF_DIR, exist_ok=True)
    with open(auth_file, "w") as f:
        json.dump({user: passwd}, f, indent=2)
    print(f"认证文件已创建: {auth_file}")
    return auth_file


def remove_auth_file():
    """删除认证文件"""
    auth_file = os.path.join(CONF_DIR, "user_to_passwd.json")
    if os.path.exists(auth_file):
        os.remove(auth_file)
        print(f"认证文件已删除: {auth_file}")


class FlowerClient:
    """Flower API 客户端"""

    def __init__(self, host: str, port: int, protocol: str = "https",
                 auth_user: str = None, auth_passwd: str = None, timeout: int = 30):
        self.base_url = f"{protocol}://{host}:{port}/flower/api"
        self.timeout = timeout
        self.auth = (
            HTTPBasicAuth(auth_user, auth_passwd)
            if auth_user and auth_passwd
            else HTTPBasicAuth("", "")
        )

    def _request(self, endpoint: str, params: dict = None) -> dict:
        """发送请求到 Flower API"""
        url = f"{self.base_url}/{endpoint}"
        resp = requests.get(
            url,
            auth=self.auth,
            params=params,
            timeout=self.timeout,
            verify=False,  # 测试环境跳过证书验证
        )
        resp.raise_for_status()
        return resp.json()

    def _post(self, endpoint: str, params: dict = None, timeout: int = 60) -> dict:
        """发送 POST 请求到 Flower API"""
        url = f"{self.base_url}/{endpoint}"
        resp = requests.post(
            url,
            auth=self.auth,
            params=params,
            timeout=timeout,
            verify=False,
        )
        resp.raise_for_status()
        return resp.json()

    def get_workers(self, refresh: bool = True) -> dict:
        """获取所有 worker 状态

        Args:
            refresh: 是否刷新获取最新状态（分布式模式下需要）

        返回示例:
        {
            "worker1": {
                "status": True,
                "completed": 100,
                "active": 2,
                "queues": ["celery"],
                "concurrency": 4,
                ...
            }
        }
        """
        params = {"refresh": 1} if refresh else None
        return self._request("workers", params)

    def get_tasks(self, state: str = None, limit: int = None, offset: int = None) -> dict:
        """获取任务列表

        Args:
            state: 过滤任务状态 (PENDING, STARTED, SUCCESS, FAILURE, RETRY, REVOKED)
            limit: 返回数量限制
            offset: 偏移量

        返回示例:
        {
            "task-uuid-1": {
                "uuid": "task-uuid-1",
                "name": "tasks.add",
                "state": "SUCCESS",
                "received": 1234567890.0,
                "started": 1234567891.0,
                "succeeded": 1234567892.0,
                "args": [1, 2],
                "kwargs": {},
                "worker": "worker1",
                "result": 3,
                "runtime": 0.5
            }
        }
        """
        params = {}
        if state:
            params["state"] = state
        if limit:
            params["limit"] = limit
        if offset:
            params["offset"] = offset
        return self._request("tasks", params)

    def get_queues(self) -> dict:
        """获取队列信息

        返回示例:
        {
            "celery": {
                "name": "celery",
                "messages": 10,
                "unacked": 2
            }
        }
        """
        return self._request("queues/length")

    def get_worker(self, worker_name: str) -> dict:
        """获取单个 worker 详情"""
        return self._request(f"workers/{worker_name}")

    def get_task(self, task_id: str) -> dict:
        """获取单个任务详情"""
        return self._request(f"tasks/{task_id}")

    def pool_grow(self, worker_name: str, count: int = 1) -> dict:
        """增加 worker 的并发数

        Args:
            worker_name: worker 名称 (如 celery@hostname)
            count: 要增加的并发数
        """
        return self._post(f"worker/pool/grow/{worker_name}", {"n": count})

    def pool_shrink(self, worker_name: str, count: int = 1) -> dict:
        """减少 worker 的并发数

        Args:
            worker_name: worker 名称 (如 celery@hostname)
            count: 要减少的并发数
        """
        return self._post(f"worker/pool/shrink/{worker_name}", {"n": count})


def print_separator(title: str = ""):
    """打印分隔符"""
    print("\n" + "=" * 60)
    if title:
        print(f"  {title}")
        print("=" * 60)


def test_flower_workers(client: FlowerClient):
    """测试获取 worker 状态"""
    print_separator("测试 Flower Workers API")

    start = time.time()
    workers = client.get_workers()
    elapsed = time.time() - start

    print(f"响应时间: {elapsed * 1000:.2f}ms")
    print(f"Worker 数量: {len(workers)}")

    for name, info in workers.items():
        print(f"\n  Worker: {name}")
        # 分布式模式下，有 stats 数据就表示在线
        is_online = info.get("stats") is not None
        print(f"    - 状态: {'在线' if is_online else '离线'}")
        stats = info.get("stats", {})
        pool = stats.get("pool", {})
        print(f"    - 并发数: {pool.get('max-concurrency', 'N/A')}")
        print(f"    - 完成任务: {stats.get('total', {}).get('total', 0) if isinstance(stats.get('total'), dict) else 0}")
        print(f"    - 活跃任务: {len(info.get('active', []))}")

    assert len(workers) > 0, "没有检测到在线的 worker"
    print("\n✅ test_flower_workers 通过")


def test_flower_tasks(client: FlowerClient):
    """测试获取任务列表"""
    print_separator("测试 Flower Tasks API")

    # 测试获取所有任务
    start = time.time()
    all_tasks = client.get_tasks()
    elapsed = time.time() - start
    print(f"获取所有任务 - 响应时间: {elapsed * 1000:.2f}ms, 数量: {len(all_tasks)}")

    # 测试按状态过滤
    for state in ["SUCCESS", "FAILURE", "PENDING", "STARTED"]:
        start = time.time()
        tasks = client.get_tasks(state=state)
        elapsed = time.time() - start
        print(f"状态 {state}: {len(tasks)} 个任务, 响应时间: {elapsed * 1000:.2f}ms")

    # 显示最近的任务详情
    if all_tasks:
        latest_task_id = list(all_tasks.keys())[0]
        task = all_tasks[latest_task_id]
        print(f"\n最近任务示例:")
        print(f"  ID: {task.get('uuid')}")
        print(f"  名称: {task.get('name')}")
        print(f"  状态: {task.get('state')}")
        print(f"  Worker: {task.get('worker')}")

    print("\n✅ test_flower_tasks 通过")


def test_flower_queues(client: FlowerClient):
    """测试获取队列信息"""
    print_separator("测试 Flower Queues API")

    start = time.time()
    queues_data = client.get_queues()
    elapsed = time.time() - start

    print(f"响应时间: {elapsed * 1000:.2f}ms")

    # API 返回格式: {"active_queues": [{"name": "xxx", "messages": 0}, ...]}
    active_queues = queues_data.get("active_queues", [])
    print(f"队列数量: {len(active_queues)}")

    for queue_info in active_queues:
        print(f"\n  队列: {queue_info.get('name')}")
        print(f"    - 待处理消息: {queue_info.get('messages', 0)}")

    print("\n✅ test_flower_queues 通过")


def test_flower_performance(client: FlowerClient, iterations: int = 10):
    """测试 Flower API 性能"""
    print_separator(f"Flower API 性能测试 ({iterations} 次迭代)")

    endpoints = [
        # workers 使用 refresh 会触发 Celery inspect，耗时约 1 秒，这是正常的
        ("workers (refresh)", lambda: client.get_workers(refresh=True)),
        ("workers (no refresh)", lambda: client.get_workers(refresh=False)),
        ("tasks", lambda: client.get_tasks()),
        ("queues", lambda: client.get_queues()),
    ]

    results = {}

    for name, func in endpoints:
        times = []
        for i in range(iterations):
            start = time.time()
            func()
            times.append(time.time() - start)

        avg_time = sum(times) / len(times)
        min_time = min(times)
        max_time = max(times)

        results[name] = {
            "avg": avg_time,
            "min": min_time,
            "max": max_time,
        }

        print(f"\n  {name}:")
        print(f"    平均: {avg_time * 1000:.2f}ms")
        print(f"    最小: {min_time * 1000:.2f}ms")
        print(f"    最大: {max_time * 1000:.2f}ms")

    # 性能断言：
    # - workers (refresh) 在分布式模式下需要 Celery inspect，允许 2 秒
    # - 其他 API 应快速响应
    assert results["workers (refresh)"]["avg"] < 2.0, "workers (refresh) 平均响应时间超过 2秒"
    assert results["workers (no refresh)"]["avg"] < 0.5, "workers (no refresh) 平均响应时间超过 500ms"
    assert results["tasks"]["avg"] < 0.5, "tasks 平均响应时间超过 500ms"
    assert results["queues"]["avg"] < 0.5, "queues 平均响应时间超过 500ms"

    print("\n✅ test_flower_performance 通过")


def show_flower_summary(client: FlowerClient):
    """展示 Flower API 聚合数据"""
    print_separator("Flower API 数据汇总")

    # Flower 方式：多次请求
    start = time.time()
    workers = client.get_workers()
    tasks = client.get_tasks()
    queues = client.get_queues()
    elapsed = time.time() - start

    # 聚合数据
    online_workers = [w for w in workers.values() if w.get("stats")]
    task_stats = {}
    for task in tasks.values():
        state = task.get("state", "UNKNOWN")
        task_stats[state] = task_stats.get(state, 0) + 1

    # 队列数据格式: {"active_queues": [{"name": "xxx", "messages": 0}, ...]}
    active_queues = queues.get("active_queues", [])
    queue_lengths = {q.get("name"): q.get("messages", 0) for q in active_queues}

    print("Flower API 汇总:")
    print(f"  在线 Workers: {len(online_workers)}")
    for name, w in workers.items():
        if w.get("stats"):
            print(f"    - {name}")
    print(f"  任务统计: {task_stats}")
    print(f"  队列长度: {queue_lengths}")
    print(f"  总耗时: {elapsed * 1000:.2f}ms (3 次请求)")

    print("\n提示: status_info 接口一次请求可获取相同数据，性能更好")
    print("      但 Flower UI 提供可视化界面，适合人工查看")
    print("\n✅ 数据汇总完成")


def test_flower_auth(client_without_auth: FlowerClient, client_with_auth: FlowerClient):
    """测试 Flower 认证"""
    print_separator("测试 Flower 认证")

    # 无认证应该失败
    try:
        client_without_auth.get_workers()
        print("⚠️ 警告: 无认证访问成功（可能未启用认证）")
    except requests.HTTPError as e:
        assert e.response.status_code == 401, f"预期 401，实际 {e.response.status_code}"
        print("✅ 无认证访问被拒绝 (401)")

    # 有认证应该成功
    try:
        workers = client_with_auth.get_workers()
        print(f"✅ 认证访问成功，获取到 {len(workers)} 个 worker")
    except requests.HTTPError as e:
        raise AssertionError(f"认证访问失败: {e}")


def test_pool_scale(client: FlowerClient):
    """测试 worker pool 扩缩容"""
    print_separator("测试 Worker Pool 扩缩容")

    # 获取当前 worker 信息
    workers = client.get_workers(refresh=True)
    if not workers:
        print("❌ 没有可用的 worker，跳过测试")
        return

    # 选择第一个 worker 进行测试
    worker_name = list(workers.keys())[0]
    stats = workers[worker_name].get("stats", {})
    pool = stats.get("pool", {})
    max_concurrency = pool.get("max-concurrency", 16)
    current_processes = len(pool.get("processes", []))

    print(f"测试 Worker: {worker_name}")
    print(f"最大并发配置: {max_concurrency}")
    print(f"当前进程数: {current_processes}")

    # 准备阶段：将进程数调整到 max_concurrency
    print(f"\n准备阶段: 调整进程数到 {max_concurrency}...")
    if current_processes < max_concurrency:
        diff = max_concurrency - current_processes
        grow_result = client.pool_grow(worker_name, diff)
        print(f"扩容请求 (+{diff}): {grow_result.get('message', grow_result)}")
    elif current_processes > max_concurrency:
        diff = current_processes - max_concurrency
        shrink_result = client.pool_shrink(worker_name, diff)
        print(f"收缩请求 (-{diff}): {shrink_result.get('message', shrink_result)}")
    else:
        print("进程数已正确，无需调整")

    time.sleep(5)  # 等待进程调整完成

    workers = client.get_workers(refresh=True)
    pool = workers[worker_name].get("stats", {}).get("pool", {})
    current_processes = len(pool.get("processes", []))
    print(f"调整后进程数: {current_processes}")

    # 1. 缩容到 0
    print("\n步骤 1: 将进程数收缩为 0...")
    shrink_result = client.pool_shrink(worker_name, current_processes)
    print(f"收缩请求: {shrink_result.get('message', shrink_result)}")
    time.sleep(5)

    workers = client.get_workers(refresh=True)
    current_pool = workers[worker_name].get("stats", {}).get("pool", {})
    current_processes = len(current_pool.get("processes", []))
    print(f"当前进程数: {current_processes}")
    assert current_processes == 0, f"收缩失败，预期 0 个进程，实际 {current_processes}"
    print("✅ 收缩成功，进程数已为 0")

    # 2. 恢复到最大并发数
    print(f"\n步骤 2: 恢复进程数到 {max_concurrency}...")
    grow_result = client.pool_grow(worker_name, max_concurrency)
    print(f"扩容请求: {grow_result.get('message', grow_result)}")
    time.sleep(5)

    workers = client.get_workers(refresh=True)
    current_pool = workers[worker_name].get("stats", {}).get("pool", {})
    current_processes = len(current_pool.get("processes", []))
    print(f"当前进程数: {current_processes}")
    assert current_processes == max_concurrency, f"扩容失败，预期 {max_concurrency} 个进程，实际 {current_processes}"
    print(f"✅ 扩容成功，进程数已恢复到 {max_concurrency}")

    print("\n✅ test_pool_scale 通过")


def main():
    """主测试入口"""
    # 配置
    HOST = "127.0.0.1"
    PORT = 9001
    AUTH_USER = "admin"
    AUTH_PASSWD = "passwd"


    # 创建客户端
    client = FlowerClient(
        host=HOST,
        port=PORT,
        auth_user=AUTH_USER,
        auth_passwd=AUTH_PASSWD,
    )

    client_without_auth = FlowerClient(
        host=HOST,
        port=PORT,
    )

    print("\n" + "=" * 60)
    print("  Flower API 测试套件")
    print("  目标: " + client.base_url)
    print("=" * 60)

    try:
        # 基础功能测试
        test_flower_workers(client)
        test_flower_tasks(client)
        test_flower_queues(client)

        # 性能测试
        test_flower_performance(client, iterations=10)

        # Pool 扩缩容测试
        test_pool_scale(client)

        # 认证测试
        test_flower_auth(client_without_auth, client)

        # 数据汇总
        show_flower_summary(client)

        print("\n" + "=" * 60)
        print("  🎉 所有测试通过!")
        print("=" * 60)

    except requests.exceptions.ConnectionError as e:
        print(f"\n❌ 连接失败: {e}")
        print("请确保 Flower 服务已启动且 FLOWER_ENABLED=true")
        raise
    except Exception as e:
        print(f"\n❌ 测试失败: {e}")
        raise


if __name__ == "__main__":
    main()