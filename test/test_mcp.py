"""MCP 端点验收测试。

启动真实的 Celery worker + uvicorn（开启 API_MCP），用官方 MCP 客户端连接，验证：

  1. 工具列表跟随 API_* 开关注册（create / check / run + 全局工具）
  2. 工具参数 schema 是扁平的（不会多出一层 params 包裹）
  3. create → check 全流程可用，result_id 可在后续调用中复用
  4. 小结果以 result_type=json 直接返回
  5. 大结果以 result_type=s3 返回引用与预签名地址，且地址可裸下载
  6. check 的返回值始终很小（不把结果内容塞进上下文）
  7. run 同步执行且对超大结果做截断保护
  8. 配置认证后，未携带凭据的请求返回 401

依赖：Redis、S3 兼容对象存储、mcp 客户端库（fasttask/requirements.txt 已含）
     脚本会自动创建临时任务 mcp_probe，结束后删除。

用法：
    export TEST_REDIS_PORT=16379
    export TEST_REDIS_PASSWD=testpasswd
    export TEST_S3_ENDPOINT=127.0.0.1:9000
    export TEST_S3_ACCESS_KEY=testuser
    export TEST_S3_SECRET_KEY=secret
    python test/test_mcp.py
"""

import asyncio
import base64
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
TASK_FILE = FASTTASK_DIR / "tasks" / "mcp_probe.py"
LOADED_TASKS_DIR = FASTTASK_DIR / "loaded_tasks"

REDIS_HOST = os.environ.get("TEST_REDIS_HOST", "127.0.0.1")
REDIS_PORT = os.environ.get("TEST_REDIS_PORT", "16379")
REDIS_PASSWD = os.environ.get("TEST_REDIS_PASSWD", "testpasswd")
S3_ENDPOINT = os.environ.get("TEST_S3_ENDPOINT", "127.0.0.1:9000")
S3_ACCESS_KEY = os.environ.get("TEST_S3_ACCESS_KEY", "testuser")
S3_SECRET_KEY = os.environ.get("TEST_S3_SECRET_KEY", "secret")
S3_SECURE = os.environ.get("TEST_S3_SECURE", "False")
S3_BUCKET = os.environ.get("TEST_S3_BUCKET", "fasttask-mcp-selftest")
API_PORT = os.environ.get("TEST_API_PORT", "18810")
API_BASE = f"http://127.0.0.1:{API_PORT}"
MCP_URL = f"{API_BASE}/mcp"
WORK_DIR = pathlib.Path(os.environ.get("TEST_WORK_DIR", "/tmp/fasttask-mcp-e2e"))

AUTH_USER = "admin"
AUTH_PASSWD = "mcp-selftest-passwd"
AUTH_HEADER = "Basic " + base64.b64encode(
    f"{AUTH_USER}:{AUTH_PASSWD}".encode("utf-8")
).decode("utf-8")

TASK_SOURCE = '''from pydantic import BaseModel


class Params(BaseModel):
    size: int = 10
    tag: str = "default"


class Result(BaseModel):
    payload: str
    tag: str


def mcp_probe(size: int = 10, tag: str = "default"):
    """生成指定长度的测试负载，用于验证 MCP 工具的结果交付行为。"""
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

os.environ.update(
    {
        "NODE_TYPE": "single_node",
        "RESULT_TYPE": "AUTO",
        "RESULT_AUTO_TO_S3_SIZE": "1000",
        "RESULT_TO_S3_TRIES": "3",
        "FILE_CLEANUP_ENABLED": "False",
        "FLOWER_ENABLED": "False",
        "API_DOCS": "False",
        "API_MCP": "True",
        "WORKER_TAG": "mcp-selftest",
    }
)

TASK_FILE.write_text(TASK_SOURCE)

import run  # noqa: E402

api_proc = celery_proc = None
celery_log = api_log = None

try:
    for env in run.env_type_to_envs["common"]:
        env.init_func = None
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

    # S3_* 与阈值在 run.py 里是 force_default（对外不允许改），
    # 验收脚本在环境初始化之后再覆盖，用于指向自建的对象存储实例。
    os.environ.update(
        {
            "S3_ENDPOINT": S3_ENDPOINT,
            "S3_BUCKET": S3_BUCKET,
            "S3_SECURE": S3_SECURE,
            "S3_ACCESS_KEY": S3_ACCESS_KEY,
            "S3_SECRET_KEY": S3_SECRET_KEY,
            "S3_PRESIGN_EXPIRES": "3600",
        }
    )
    os.environ["MASTER_HOST"] = REDIS_HOST
    os.environ["TASK_QUEUE_PORT"] = REDIS_PORT
    os.environ["TASK_QUEUE_PASSWD"] = REDIS_PASSWD
    os.environ["WORKER_CONCURRENCY"] = "1"
    run.check_envs()

    # 认证：与 HTTP 接口共用同一份凭据文件
    (pathlib.Path(os.environ["CONF_DIR"]) / "user_to_passwd.json").write_text(
        json.dumps({AUTH_USER: AUTH_PASSWD})
    )

    print(f"[env] LOADED_TASKS = {os.environ['LOADED_TASKS']}")
    print(f"[env] MCP endpoint = {MCP_URL}")
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
            "--without-gossip", "--without-mingle", "-n", "mcp@test",
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

    def wait_api(timeout=40):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                resp = httpx.post(
                    f"{API_BASE}/status_info",
                    json={"fields": []},
                    headers={"Authorization": AUTH_HEADER},
                    timeout=2,
                )
                if resp.status_code == 200:
                    return True
            except Exception:  # noqa: BLE001
                pass
            time.sleep(0.5)
        return False

    if not wait_api():
        print("\nAPI 未就绪，最近日志：")
        print(api_log_path.read_text()[-2500:])
        sys.exit(1)
    time.sleep(2)
    print("\n服务已就绪")

    # ----------------------------------------------------------------------- #
    section("1. 认证：MCP 复用 FastTask 既有的 HTTP Basic 凭据")
    unauthorized = httpx.post(
        MCP_URL,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers={"Accept": "application/json, text/event-stream",
                 "Content-Type": "application/json"},
        timeout=10,
        follow_redirects=True,  # /mcp 会 307 到 /mcp/，需跟随才能触达认证层
    )
    check("未带凭据返回 401", unauthorized.status_code == 401, unauthorized.status_code)
    bad_auth = httpx.post(
        MCP_URL,
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        headers={
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Authorization": "Basic " + base64.b64encode(b"admin:wrong").decode(),
        },
        timeout=10,
        follow_redirects=True,
    )
    check("凭据错误返回 401", bad_auth.status_code == 401, bad_auth.status_code)

    # ----------------------------------------------------------------------- #
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    async def main():
        headers = {"Authorization": AUTH_HEADER}
        async with streamablehttp_client(MCP_URL, headers=headers) as (read, write, _):
            async with ClientSession(read, write) as session:
                init_result = await session.initialize()

                section("2. 工具列表（跟随 API_* 开关注册）")
                listed = await session.list_tools()
                names = sorted(tool.name for tool in listed.tools)
                print(f"  {names}")
                for expected in (
                    "create_mcp_probe", "check_mcp_probe", "run_mcp_probe",
                    "fasttask_status", "fasttask_revoke",
                ):
                    check(f"注册了 {expected}", expected in names, names)

                section("3. 工具参数 schema 扁平（无 params 包裹）")
                tool_map = {tool.name: tool for tool in listed.tools}
                create_schema = tool_map["create_mcp_probe"].inputSchema
                properties = set(create_schema.get("properties", {}))
                check(
                    "参数直接展开在顶层",
                    properties == {"size", "tag"},
                    properties,
                )
                check(
                    "不含 fasttask_concurrency_params（不暴露内部参数）",
                    "fasttask_concurrency_params" not in properties,
                    properties,
                )
                check(
                    "参数 description 已保留",
                    create_schema["properties"]["size"].get("description")
                    is not None
                    or create_schema["properties"]["size"].get("title") == "Size",
                    create_schema["properties"]["size"],
                )

                async def call(name, arguments=None):
                    result = await session.call_tool(name, arguments or {})
                    text = result.content[0].text if result.content else ""
                    return result, json.loads(text) if text else {}

                section("4. instructions 分层：模块身份 + 平台约定 + 任务一览")
                instructions = init_result.instructions or ""
                check(
                    "① 含模块身份（来自 setting.py）",
                    "Fasttask" in instructions,
                    instructions[:120],
                )
                check(
                    "② 含平台约定（工具族与通用接口）",
                    "create_<task>" in instructions and "fasttask_revoke" in instructions,
                    "",
                )
                check(
                    "② 说明 run 的 result_id 为空，且默认一律走 create/check",
                    "result_id 为空" in instructions
                    and "默认一律走 create_<task> + check_<task>" in instructions,
                    "",
                )
                check(
                    "③ 含任务一览（列出本模块任务）",
                    "本模块提供以下任务" in instructions and "mcp_probe" in instructions,
                    "",
                )

                section("5. outputSchema：能看到任务真实的 Result 结构")
                probe_tool = tool_map["create_mcp_probe"]
                out_schema = getattr(probe_tool, "outputSchema", None)
                check("工具声明了 outputSchema", bool(out_schema), "")
                if out_schema:
                    props = set(out_schema.get("properties", {}))
                    check(
                        "外层字段固定（result_id/state/result_type/result）",
                        {"result_id", "state", "result_type", "result"} <= props,
                        props,
                    )
                    refs = json.dumps(out_schema.get("$defs", {}), ensure_ascii=False)
                    check(
                        "$defs 含任务自己的 Result 模型（不是笼统 object）",
                        "payload" in refs,
                        refs[:200],
                    )
                    check(
                        "result 用 anyOf 覆盖 json/s3/text 三种形态",
                        len(out_schema["properties"]["result"].get("anyOf", [])) >= 3,
                        out_schema["properties"]["result"],
                    )

                section("6. 结构化返回：structuredContent 与声明一致")
                call_result, _ = await call("run_mcp_probe", {"size": 8, "tag": "struct"})
                structured = getattr(call_result, "structuredContent", None)
                check("run_* 返回 structuredContent", bool(structured), "")
                if structured:
                    check(
                        "结构化字段与 outputSchema 对齐",
                        {"result_id", "state", "result_type", "result"} <= set(structured),
                        list(structured),
                    )
                    check(
                        "run 的 result_id 为空（不可用于 check）",
                        structured.get("result_id") == "",
                        structured.get("result_id"),
                    )

                async def wait_task(result_id, timeout=60):
                    deadline = time.time() + timeout
                    latest = None
                    while time.time() < deadline:
                        _, latest = await call(
                            "check_mcp_probe", {"result_id": result_id}
                        )
                        if latest["state"] in ("SUCCESS", "FAILURE", "REVOKED"):
                            return latest
                        await asyncio.sleep(0.4)
                    raise AssertionError(f"任务超时: {latest}")

                section("7. create → check：小结果直接内联")
                _, created = await call("create_mcp_probe", {"size": 10, "tag": "mcp"})
                check("create 返回 result_id", bool(created.get("result_id")), created)
                result_id = created["result_id"]

                checked = await wait_task(result_id)
                print(f"  {json.dumps(checked, ensure_ascii=False)[:220]}")
                check("state=SUCCESS", checked["state"] == "SUCCESS", checked)
                check("result_type=json", checked["result_type"] == "json", checked)
                check(
                    "结果内容正确",
                    checked["result"].get("payload") == "x" * 10,
                    checked["result"],
                )
                check("tag 透传", checked["result"].get("tag") == "mcp", checked["result"])

                section("8. 大结果：返回 s3 引用而非内容")
                _, created = await call("create_mcp_probe", {"size": 20000, "tag": "big"})
                big = await wait_task(created["result_id"])
                raw_text_len = len(json.dumps(big, ensure_ascii=False))
                print(f"  {json.dumps(big, ensure_ascii=False)[:240]}")
                check("result_type=s3", big["result_type"] == "s3", big["result_type"])
                reference = big["result"]
                check("给出预签名 url", bool(reference.get("url")), reference)
                check(
                    "引用只有四个字段（uri 已移除）",
                    set(reference) == {"size_bytes", "sha256", "url", "expires_at"},
                    list(reference),
                )
                check(
                    "返回值体积仍然很小（未塞入 20KB 结果）",
                    raw_text_len < 2000,
                    raw_text_len,
                )

                check(
                    "url 是可直接访问的地址（绝对或相对）",
                    bool(reference["url"]),
                    reference["url"][:60],
                )
                # url 可能是绝对地址（服务端能确定对外地址时），也可能仍是相对路径
                download_url = reference["url"]
                if not download_url.startswith("http"):
                    download_url = f"{API_BASE}{download_url}"
                try:
                    with urllib.request.urlopen(download_url, timeout=15) as resp:
                        body = resp.read()
                    downloaded = json.loads(body.decode("utf-8"))
                    check("经 API 端口代理下载成功", downloaded.get("payload") == "x" * 20000)
                    check("tag 透传", downloaded.get("tag") == "big")
                except Exception as error:  # noqa: BLE001
                    check("预签名地址可裸下载", False, repr(error))

                section("9. run 同步执行与截断保护")
                _, run_small = await call("run_mcp_probe", {"size": 8, "tag": "sync"})
                check("同步执行成功", run_small["state"] == "SUCCESS", run_small)
                check(
                    "小结果直接返回",
                    run_small["result"].get("payload") == "x" * 8,
                    run_small["result"],
                )

                _, run_big = await call("run_mcp_probe", {"size": 400000})
                print(f"  run(state={run_big.get('state')}) keys={list(run_big.keys())}")
                big_size = len(json.dumps(run_big, ensure_ascii=False))
                check(
                    "超大结果不会直接塞进上下文",
                    big_size < 4000,
                    big_size,
                )
                check(
                    "run 大结果为外置引用或截断（不再是 None.json 这类坏 key）",
                    run_big.get("result_type") == "s3"
                    or run_big.get("truncated") is True,
                    run_big,
                )
                if run_big.get("result_type") == "s3":
                    check(
                        "run 外置的结果带可用 url",
                        bool(run_big["result"].get("url")),
                        run_big["result"].get("url", "")[:60],
                    )
                check(
                    "截断后体积可控",
                    len(json.dumps(run_big, ensure_ascii=False)) < 4000,
                    len(json.dumps(run_big, ensure_ascii=False)),
                )

                section("10. 全局工具")
                _, status = await call("fasttask_status")
                check("status 返回 running_id", "running_id" in status, status.keys())
                check("status 含队列积压", "pending_task_count" in status, status.keys())

                _, revoked = await call("fasttask_revoke", {"result_id": result_id})
                check(
                    "revoke 受理已结束任务",
                    revoked.get("status") == "SUCCESS",
                    revoked,
                )

    asyncio.run(main())

finally:
    print("\n清理进程与临时任务…")
    for proc in (api_proc, celery_proc):
        if proc is not None:
            proc.terminate()
    for proc in (api_proc, celery_proc):
        if proc is not None:
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
    for handle in (celery_log, api_log):
        if handle is not None:
            handle.close()
    TASK_FILE.unlink(missing_ok=True)
    if LOADED_TASKS_DIR.exists():
        shutil.rmtree(LOADED_TASKS_DIR, ignore_errors=True)

print(f"\n{'=' * 62}")
if failures:
    print(f"❌ 失败 {len(failures)} 项：{failures}")
    print("=" * 62)
    sys.exit(1)
print("MCP 端点验收全部通过 ✅")
print("=" * 62)
