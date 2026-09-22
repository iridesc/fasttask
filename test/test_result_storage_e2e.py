"""结果存储层端到端验收测试。

启动真实的 Celery worker + uvicorn API，连真实 Redis 与 S3 兼容对象存储，验证：
  1. 小结果 -> result_type=json（内联）
  2. 大结果 -> result_type=s3（外置）+ 预签名裸 URL 下载 + sha256 校验
  3. 结果结构与 Result 不符 -> 任务 FAILURE + result_type=text + traceback
  4. result_type 三种取值齐备
  5. Params 非法 -> API 层 422（不进任务）

脚本会自动在 tasks/ 下创建临时任务 e2e_payload，结束后删除，不留残留。

依赖：Redis、S3 兼容对象存储（MinIO / versitygw / Ceph）均已就绪，
     且已安装 fasttask/requirements.txt 中的依赖。

用法：
    export TEST_REDIS_HOST=127.0.0.1
    export TEST_REDIS_PORT=16379
    export TEST_REDIS_PASSWD=testpasswd
    export TEST_S3_ENDPOINT=127.0.0.1:9000
    export TEST_S3_ACCESS_KEY=testuser
    export TEST_S3_SECRET_KEY=secret
    python test/test_result_storage_e2e.py
"""

import hashlib
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import urllib.request

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
FASTTASK_DIR = REPO_DIR / "fasttask"
TASK_FILE = FASTTASK_DIR / "tasks" / "e2e_payload.py"
LOADED_TASKS_DIR = FASTTASK_DIR / "loaded_tasks"

REDIS_HOST = os.environ.get("TEST_REDIS_HOST", "127.0.0.1")
REDIS_PORT = os.environ.get("TEST_REDIS_PORT", "16379")
REDIS_PASSWD = os.environ.get("TEST_REDIS_PASSWD", "testpasswd")
S3_PORT = os.environ.get("TEST_S3_PORT", "9000")
# 对象存储地址由代码按节点类型推导（single_node → 127.0.0.1:{S3_PORT}），
# 这里保留一份副本，仅用于打印/拼下载 URL。
S3_ENDPOINT = f"127.0.0.1:{S3_PORT}"
S3_ACCESS_KEY = os.environ.get("TEST_S3_ACCESS_KEY", "testuser")
S3_SECRET_KEY = os.environ.get("TEST_S3_SECRET_KEY", "secret")
S3_SECURE = os.environ.get("TEST_S3_SECURE", "False")
S3_BUCKET = os.environ.get("TEST_S3_BUCKET", "fasttask-result-selftest")
API_PORT = os.environ.get("TEST_API_PORT", "18800")
API_BASE = f"http://127.0.0.1:{API_PORT}"
WORK_DIR = pathlib.Path(os.environ.get("TEST_WORK_DIR", "/tmp/fasttask-result-e2e"))

TASK_SOURCE = '''from pydantic import BaseModel


class Params(BaseModel):
    size: int = 10
    tag: str = "default"
    break_result: bool = False


class Result(BaseModel):
    payload: str
    tag: str


def e2e_payload(size: int = 10, tag: str = "default", break_result: bool = False):
    if break_result:
        # 故意少返回 tag，用于验证生产端 Result 校验
        return {"payload": "x" * size}
    return {"payload": "x" * size, "tag": tag}
'''

failures = []


def section(title):
    print(f"\n{'=' * 62}\n{title}\n{'=' * 62}")


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}{'' if cond else f'  -> {detail}'}")
    if not cond:
        failures.append(name)


os.chdir(FASTTASK_DIR)
sys.path.insert(0, str(FASTTASK_DIR))

# --------------------------------------------------------------------------- #
# 环境准备
# --------------------------------------------------------------------------- #
os.environ.update(
    {
        "NODE_TYPE": "single_node",
        "RESULT_TYPE": "AUTO",
        "RESULT_AUTO_TO_S3_SIZE": "1000",
        "RESULT_TO_S3_TRIES": "3",
        "FILE_CLEANUP_ENABLED": "False",
        "FLOWER_ENABLED": "False",
        "API_DOCS": "False",
        "WORKER_TAG": "e2e",
    }
)

TASK_FILE.write_text(TASK_SOURCE)

import run  # noqa: E402

try:
    for env in run.env_type_to_envs["common"]:
        env.init_func = None  # 容器内路径 /fasttask 本地不可写
        env.init_env()
    for env in run.env_type_to_envs["single_node"]:
        env.init_func = None
        env.init_env()

    os.environ["FASTTASK_DIR"] = str(FASTTASK_DIR)
    os.environ["FILES_DIR"] = str(WORK_DIR / "files")
    os.environ["FASTTASK_FILES_DIR"] = str(WORK_DIR / "files/fasttask")
    os.environ["LOG_DIR"] = str(WORK_DIR / "files/fasttask/log")
    os.environ["CONF_DIR"] = str(WORK_DIR / "files/fasttask/conf")
    os.environ["SSL_CERT_DIR"] = str(WORK_DIR / "files/fasttask/ssl_cert")
    os.environ["REDIS_DIR"] = str(WORK_DIR / "files/fasttask/redis")
    os.environ["SSL_KEYFILE"] = str(WORK_DIR / "files/fasttask/ssl_cert/key.pem")
    os.environ["SSL_CERTFILE"] = str(WORK_DIR / "files/fasttask/ssl_cert/cert.pem")
    os.environ["CELERY_DIR"] = str(WORK_DIR / "files/fasttask/celery")
    os.environ["LAZY_ACTION_FILE_PATH"] = str(WORK_DIR / "files/fasttask/lazy_action")
    for _path in (
        os.environ["FILES_DIR"],
        os.environ["FASTTASK_FILES_DIR"],
        os.environ["LOG_DIR"],
        os.environ["CONF_DIR"],
        os.environ["SSL_CERT_DIR"],
        os.environ["REDIS_DIR"],
        os.environ["CELERY_DIR"],
        os.environ["LAZY_ACTION_FILE_PATH"],
    ):
        pathlib.Path(_path).mkdir(parents=True, exist_ok=True)

    # single_node 分支里 MASTER_HOST / TASK_QUEUE_* 是 force_default，这里指向测试 Redis
    # S3_* 与阈值在 run.py 里是 force_default（对外不允许改），
    # 验收脚本在环境初始化之后再覆盖，用于指向自建的对象存储实例。
    os.environ.update(
        {
            "NODE_TYPE": "single_node",
            "S3_PORT": S3_PORT,
            "S3_BUCKET": S3_BUCKET,
            "S3_SECURE": S3_SECURE,
            "S3_ACCESS_KEY": S3_ACCESS_KEY,
            "S3_SECRET_KEY": S3_SECRET_KEY,
            "RESULT_EXPIRES": "3600",
        }
    )
    os.environ["MASTER_HOST"] = REDIS_HOST
    os.environ["TASK_QUEUE_PORT"] = REDIS_PORT
    os.environ["TASK_QUEUE_PASSWD"] = REDIS_PASSWD
    os.environ["WORKER_CONCURRENCY"] = "1"
    run.check_envs()

    print(f"[env] LOADED_TASKS = {os.environ['LOADED_TASKS']}")
    print(f"[env] redis        = {REDIS_HOST}:{REDIS_PORT}")
    print(f"[env] s3           = {S3_ENDPOINT} / {S3_BUCKET}")

    import httpx  # noqa: E402

    env = os.environ.copy()
    celery_log_path = WORK_DIR / "celery.log"
    api_log_path = WORK_DIR / "api.log"
    celery_log = open(celery_log_path, "w")
    api_log = open(api_log_path, "w")

    celery_proc = subprocess.Popen(
        [
            sys.executable, "-m", "celery", "-A", "celery_app", "worker",
            "--without-gossip", "--without-mingle", "-n", "e2e@test",
            "-l", "INFO", "-c", "1",
        ],
        cwd=FASTTASK_DIR, env=env, stdout=celery_log, stderr=subprocess.STDOUT,
    )
    api_proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "api:app",
            "--host", "127.0.0.1", "--port", API_PORT, "--log-level", "warning",
        ],
        cwd=FASTTASK_DIR, env=env, stdout=api_log, stderr=subprocess.STDOUT,
    )

    def wait_api(timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                # /status_info 是 POST 接口（httpx.get 不接受 json 参数）
                if httpx.post(
                    f"{API_BASE}/status_info", json={"fields": []}, timeout=2
                ).status_code == 200:
                    return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.5)
        return False

    def run_task(params, timeout=60):
        response = httpx.post(
            f"{API_BASE}/create/e2e_payload", json=params, timeout=10
        )
        response.raise_for_status()
        created = response.json()
        assert created.get("id"), f"未拿到 result_id: {created}"

        deadline = time.time() + timeout
        latest = None
        while time.time() < deadline:
            latest = httpx.get(
                f"{API_BASE}/check/e2e_payload",
                params={"result_id": created["id"]},
                timeout=10,
            ).json()
            if latest["state"] in ("SUCCESS", "FAILURE", "REVOKED"):
                return created["id"], latest
            time.sleep(0.4)
        raise AssertionError(f"任务超时: {latest}")

    try:
        if not wait_api():
            print("\nAPI 未就绪，最近日志：")
            print(api_log_path.read_text()[-2000:])
            sys.exit(1)
        time.sleep(2)  # 等 worker 完成注册
        print("\n服务已就绪")

        section("1. 小结果（低于阈值）应内联为 json")
        result_id, response = run_task({"size": 10, "tag": "small"})
        print(f"  {json.dumps(response, ensure_ascii=False)[:220]}")
        check("state=SUCCESS", response["state"] == "SUCCESS", response)
        check("result_type=json", response["result_type"] == "json", response["result_type"])
        check("result 内容正确", response["result"].get("payload") == "x" * 10, response["result"])
        check("tag 透传", response["result"].get("tag") == "small", response["result"])

        section("2. 大结果（超过阈值）应外置到对象存储")
        result_id, response = run_task({"size": 5000, "tag": "big"})
        print(f"  {json.dumps(response, ensure_ascii=False)[:260]}")
        check("state=SUCCESS", response["state"] == "SUCCESS", response)
        check("result_type=s3", response["result_type"] == "s3", response["result_type"])
        reference = response["result"]
        check("返回预签名 url", bool(reference.get("url")), reference)
        check("size_bytes 合理", (reference.get("size_bytes") or 0) > 5000, reference)
        check(
            "引用只含四个字段（无 uri / url_error / hint）",
            set(reference) == {"size_bytes", "sha256", "url", "expires_at"},
            list(reference),
        )

        # 服务端能确定对外地址时直接给出完整 URL（可直接下载）；拿不到时才是相对路径。
        # 两种形态都要能正常使用。
        is_absolute = reference["url"].startswith("http")
        check("url 是可直接访问的地址（绝对或相对）", bool(reference["url"]), reference["url"][:60])
        download_url = reference["url"] if is_absolute else f"{API_BASE}{reference['url']}"
        try:
            with urllib.request.urlopen(download_url, timeout=15) as raw:
                body = raw.read()
            check("经 API 端口代理下载成功（无凭据）", True)
            check("字节数一致", len(body) == reference["size_bytes"])
            check("sha256 一致", hashlib.sha256(body).hexdigest() == reference["sha256"])
            downloaded = json.loads(body.decode("utf-8"))
            check("内容正确", downloaded.get("payload") == "x" * 5000)
            check("tag 透传", downloaded.get("tag") == "big")
        except Exception as error:  # noqa: BLE001
            check("经 API 端口代理下载成功（无凭据）", False, repr(error))

        section("3. Result 结构不符 -> 任务 FAILURE（fail-fast）")
        result_id, response = run_task({"size": 10, "break_result": True})
        print(f"  state={response['state']} result_type={response['result_type']}")
        print(f"  result[:160]={(response['result'] or '')[:160]}")
        check("state=FAILURE", response["state"] == "FAILURE", response["state"])
        check("result_type=text", response["result_type"] == "text", response["result_type"])
        check("traceback 可见", "Traceback" in (response["result"] or ""), response["result"])
        check("错误与缺失字段相关", "tag" in (response["result"] or ""), response["result"])

        section("4. result_type 三种取值齐备")
        seen = set()
        for params in ({"size": 5}, {"size": 5000}, {"size": 10, "break_result": True}):
            _, response = run_task(params)
            seen.add(response["result_type"])
        check("json / s3 / text 均出现", seen == {"json", "s3", "text"}, seen)

        section("5. Params 非法时在 API 层被拦下（422，不进任务）")
        bad = httpx.post(
            f"{API_BASE}/create/e2e_payload",
            json={"size": "not-a-number"},
            timeout=10,
        )
        print(f"  HTTP {bad.status_code}")
        check("status=422", bad.status_code == 422, bad.status_code)

        section("6. /run 同步执行：超过阈值时同样外置，避免大结果堆进调用方上下文")
        # 测试环境阈值是 1 字节，所以 size=5000 必然外置；
        # 小结果（未超阈值）仍内联，见上一节的 run 用例。
        # 未开外置（RESULT_TYPE=JSON）时 run 始终内联，行为与历史版本一致。
        run_resp = httpx.post(
            f"{API_BASE}/run/e2e_payload",
            json={"size": 5000, "tag": "sync"},
            timeout=30,
        ).json()
        print(f"  state={run_resp['state']} result_type={run_resp['result_type']}")
        check("state=SUCCESS", run_resp["state"] == "SUCCESS", run_resp)
        check(
            "result_type=s3（超过阈值时 run 也外置）",
            run_resp["result_type"] == "s3",
            run_resp["result_type"],
        )
        check(
            "result 是引用而非内容",
            isinstance(run_resp["result"], dict) and "url" in run_resp["result"],
            str(run_resp["result"])[:120],
        )
        reference = run_resp["result"]
        # url 可能是绝对地址（服务端能确定对外地址时），也可能仍是相对路径
        _url = reference["url"]
        if not _url.startswith("http"):
            _url = f"{API_BASE}{_url}"
        downloaded = httpx.get(_url, timeout=30)
        check("外置结果可经代理下载", downloaded.status_code == 200, downloaded.status_code)
        stored = downloaded.json()
        check(
            "下载内容正确",
            stored.get("payload") == "x" * 5000,
            str(stored)[:120],
        )
        check("tag 透传", stored.get("tag") == "sync", str(stored)[:120])

        section("7. MCP 端点默认启用（API_MCP 默认 True，且不影响其它接口）")
        mcp_resp = httpx.post(
            f"{API_BASE}/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "selftest", "version": "1"},
                },
            },
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            timeout=10,
            follow_redirects=False,  # /mcp 不再 307 重定向
        )
        print(f"  POST /mcp -> HTTP {mcp_resp.status_code}")
        check("默认启用 MCP 端点", mcp_resp.status_code == 200, mcp_resp.status_code)
        check(
            "POST /mcp 不再 307 重定向",
            "location" not in mcp_resp.headers,
            mcp_resp.headers.get("location"),
        )

    finally:
        print("\n清理进程与临时任务…")
        for proc in (api_proc, celery_proc):
            proc.terminate()
        for proc in (api_proc, celery_proc):
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        celery_log.close()
        api_log.close()
finally:
    # 无论成败都清掉临时任务，避免污染仓库
    TASK_FILE.unlink(missing_ok=True)
    if LOADED_TASKS_DIR.exists():
        shutil.rmtree(LOADED_TASKS_DIR, ignore_errors=True)

print(f"\n{'=' * 62}")
if failures:
    print(f"❌ 失败 {len(failures)} 项：{failures}")
    print("=" * 62)
    sys.exit(1)
print("结果存储层端到端验收全部通过 ✅")
print("=" * 62)
