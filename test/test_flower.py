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

    def get_workers(self) -> dict:
        """获取所有 worker 状态

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
        return self._request("workers")

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
        return self._request("queues")

    def get_worker(self, worker_name: str) -> dict:
        """获取单个 worker 详情"""
        return self._request(f"workers/{worker_name}")

    def get_task(self, task_id: str) -> dict:
        """获取单个任务详情"""
        return self._request(f"tasks/{task_id}")


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
        print(f"    - 状态: {'在线' if info.get('status') else '离线'}")
        print(f"    - 并发数: {info.get('pool', {}).get('max-concurrency', 'N/A')}")
        print(f"    - 完成任务: {info.get('total', 0)}")
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
    queues = client.get_queues()
    elapsed = time.time() - start

    print(f"响应时间: {elapsed * 1000:.2f}ms")
    print(f"队列数量: {len(queues)}")

    for name, info in queues.items():
        print(f"\n  队列: {name}")
        print(f"    - 待处理消息: {info.get('messages', 0)}")
        print(f"    - 未确认消息: {info.get('unacked', 0)}")

    print("\n✅ test_flower_queues 通过")


def test_flower_performance(client: FlowerClient, iterations: int = 10):
    """测试 Flower API 性能"""
    print_separator(f"Flower API 性能测试 ({iterations} 次迭代)")

    endpoints = [
        ("workers", lambda: client.get_workers()),
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

    # 性能断言：平均响应时间应小于 500ms
    for name, metrics in results.items():
        assert metrics["avg"] < 0.5, f"{name} 平均响应时间超过 500ms"

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
    online_workers = [w for w in workers.values() if w.get("status")]
    task_stats = {}
    for task in tasks.values():
        state = task.get("state", "UNKNOWN")
        task_stats[state] = task_stats.get(state, 0) + 1
    queue_lengths = {k: v.get("messages", 0) for k, v in queues.items()}

    print("Flower API 汇总:")
    print(f"  在线 Workers: {len(online_workers)}")
    for w in online_workers:
        print(f"    - {w.get('hostname', 'unknown')}")
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


def main():
    """主测试入口"""
    # 配置
    HOST = "127.0.0.1"
    PORT = 9001
    AUTH_USER = ""
    AUTH_PASSWD = ""


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