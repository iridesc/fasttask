import json
import os
import time
from fasttask_manager.manager import Manager
from requests import HTTPError
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from fasttask_manager.manager import Manager
from requests import HTTPError
from logging import getLogger

logger = getLogger(__name__)


auth_user = "admin"
auth_passwd = "passwd"

CONCURRENCY_LEVEL = 100  # 同时运行的线程/用户数量
REQUESTS_PER_THREAD = 100  # 每个线程发送的请求数量
TOTAL_REQUESTS = CONCURRENCY_LEVEL * REQUESTS_PER_THREAD  # 总请求量 (1000)


auth_file = "samples/files/fasttask/conf/user_to_passwd.json"

manager_params = {
    "host": "127.0.0.1",
    "port": "9001",
    "protocol": "https",
    "task_name": "get_circle_area",
    "tries": 1,
    "logger": logger,
}
manager = Manager(**manager_params)
authed_manager = Manager(
    **manager_params,
    auth_user=auth_user,
    auth_passwd=auth_passwd,
)
logger.setLevel("WARNING")


data = {
    "r": 1,
}


def test_run(manager=manager):
    resp = manager.run(data)
    print(f"{resp=}")
    assert resp["result"]["area"] == 3.141592653589793
    print("test_run pass")


def test_create_check(manager=manager):
    result = manager.create_and_wait_result(data)
    print(f"{result=}")
    assert result["area"] == 3.141592653589793
    print("test_run pass")


def test_upload_download(manager=manager):
    with open("lp.bin", "wb") as f:
        for i in range(1024 * 120):
            f.write(b"0" * 1024)
    file_name = manager.upload("lp.bin")
    manager.download(file_name, "downloaded-lp.bin")
    assert (
        os.path.exists("downloaded-lp.bin")
        and os.path.getsize("downloaded-lp.bin") == 1024 * 120 * 1024
    )
    os.remove("lp.bin")
    os.remove("downloaded-lp.bin")
    print("test_upload_download pass")


def test_status_info(manager=manager):
    # manager.
    ...


def test_revoke(manager=manager):
    result_ids = []
    for _ in range(10):
        result_ids.append(manager.create_task(data)["id"])

    for result_id in result_ids:
        manager.revoke(result_id)
    time.sleep(5)
    for result_id in result_ids:
        state = manager.check(result_id)["state"]
        print(f"{result_id=} {state=}")
        assert state == "REVOKED", "task not revoked"


def test_all_api(manager, expect_access=True):
    for f in [
        test_run,
        test_create_check,
        test_upload_download,
        # test_status_info,
        test_revoke,
    ]:
        try:
            f(manager=manager)
            is_access = True
        except HTTPError as e:
            if e.response.status_code == 401:
                is_access = False
            else:
                raise e
        assert is_access == expect_access


def create_auth_file():
    with open(auth_file, "w") as f:
        json.dump(
            {
                auth_user: auth_passwd,
            },
            f,
            indent=2,
        )
    print(f"auth file created")


def test_auth():
    create_auth_file()
    print(f"auth file created,")
    wait(60)
    test_all_api(manager, expect_access=False)
    print("pass. prohibited unauthorized requests")

    test_all_api(authed_manager, expect_access=True)
    print("pass. allowed authed requests")

    os.remove(auth_file)
    print("auth file removed, wait for reload")
    wait(60)


def test_wrong_running_id_request(manager: Manager):
    resp = manager.check("wrong_id")
    assert resp["state"] == "FAILURE"
    assert "RUNNING_ID" in resp["result"]


def run_single_task_metadata(manager: Manager, request_id: int):
    """
    单个线程执行任务的核心逻辑：仅创建任务和检查状态。
    """
    start_time = time.time()
    task_id = None

    try:
        task_id = manager.create_task(data)["id"]
        manager.check(task_id)["state"]
        manager.revoke(task_id)
        manager.check(task_id)["state"]
        return time.time() - start_time, True  # (耗时, 成功)

    except Exception as e:
        # 捕获任何异常，包括 HTTPError
        print(f"\n[ERROR] Request {request_id} (Task ID: {task_id}) failed: {e}")
        return time.time() - start_time, False  # (耗时, 失败)


def test_concurrency_metadata_performance(unauthed_manager: Manager):
    """
    测试服务在高并发下处理 create_task 和 check 接口的性能。
    """

    print("\n" + "=" * 50)
    print(f"🚀 开始元数据接口并发测试：总请求量 {TOTAL_REQUESTS}")
    print(f"   并发数: {CONCURRENCY_LEVEL}，每线程请求: {REQUESTS_PER_THREAD}")
    print("=" * 50)

    start_global_time = time.time()
    all_futures = []

    # 使用 ThreadPoolExecutor 管理并发线程
    with ThreadPoolExecutor(max_workers=CONCURRENCY_LEVEL) as executor:
        request_counter = 0
        for _ in range(CONCURRENCY_LEVEL):
            for _ in range(REQUESTS_PER_THREAD):
                future = executor.submit(
                    run_single_task_metadata, unauthed_manager, request_counter
                )
                all_futures.append(future)
                request_counter += 1

    # 等待所有任务完成并收集结果
    total_time = 0
    success_count = 0

    for future in as_completed(all_futures):
        try:
            # future.result() 会返回 run_single_task_metadata 的返回值
            task_time, success = future.result(timeout=60)
            total_time += task_time
            if success:
                success_count += 1
        except Exception as e:
            print(f"[CRITICAL] Concurrency task failed unexpectedly: {e}")

    end_global_time = time.time()

    # --- 性能指标计算 ---
    elapsed_time = end_global_time - start_global_time
    average_response_time = total_time / TOTAL_REQUESTS if TOTAL_REQUESTS > 0 else 0
    throughput = TOTAL_REQUESTS / elapsed_time if elapsed_time > 0 else 0

    # --- 报告与断言 ---
    print("\n" + "-" * 50)
    print("📊 性能测试结果 (create_task + check)")
    print("-" * 50)
    print(f"总请求数 (Total):      {TOTAL_REQUESTS} (每次包含 create+check)")
    print(f"成功请求数 (Success):  {success_count}")
    print(f"总耗时 (Elapsed Time): {elapsed_time:.3f} 秒")
    print(f"平均响应时间 (Avg RT): {average_response_time:.4f} 秒/请求组")
    print(f"吞吐率 (RPS):          {throughput:.2f} 请求组/秒")

    # 核心断言：所有请求组必须成功
    assert success_count == TOTAL_REQUESTS, (
        f"并发测试失败：{TOTAL_REQUESTS - success_count} 个请求组失败。"
    )
    assert throughput > 50, "吞吐率低于 50 请求组/秒。"
    # 根据需要添加性能阈值断言
    # assert average_response_time < 0.1, "平均响应时间超过阈值 0.1 秒。"

    print("-" * 50)
    print("test_concurrency_metadata_performance pass")


# todo
# test status info
# redis connection test
# release redis data


def wait(n, f=None):
    for i in range(n):
        if f and f():
            print("ready. continue...")
            return
        print(f"wait {i}/{n}...")
        time.sleep(1)


def is_ready(): ...


if __name__ == "__main__":
    for compose in [
        "samples/docker-compose-single_node.yml",
        "samples/docker-compose-distributed.yml",
        "samples/docker-compose-desktop-single_node.yml",
        "samples/docker-compose-desktop-distributed.yml",
    ]:
        print("-" * 20)
        print(f"testing {compose}...")
        print("-" * 20)
        print()

        os.system(f"docker compose -f '{compose}' up -d")
        wait(10)

        test_auth()
        test_all_api(manager)
        test_concurrency_metadata_performance(manager)

        os.system(f"docker compose -f '{compose}' down")

        print("-" * 20)
        print(f"all passed! {compose=}")
        print("-" * 20)
        print()
