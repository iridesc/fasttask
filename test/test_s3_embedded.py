"""内嵌对象存储（versitygw）验收测试。

验证阶段 2 的编排能力：

  1. start_s3.sh 按 RESULT_TYPE 决定是否启动服务
  2. start_s3.sh 派生出的凭据与 Python 侧 derive_s3_credentials 完全一致
     —— 这是 master 与所有 worker 无需额外配置即可互认的前提
  3. 用 Python 侧凭据可正常读写、生成预签名下载地址
  4. assemble_supervisor_conf 按 NODE_TYPE / RESULT_TYPE 正确组装进程配置

依赖：
  - versitygw 可执行文件（在 PATH 中，或用 TEST_VERSITYGW_BIN 指定）
  - 已安装 fasttask/requirements.txt 中的依赖

用法：
    export TEST_VERSITYGW_BIN=/usr/local/bin/versitygw   # 可选
    export TEST_S3_PORT=10911                            # 可选
    python test/test_s3_embedded.py
"""

import hashlib
import http.client
import json
import os
import pathlib
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import timedelta

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
FASTTASK_DIR = REPO_DIR / "fasttask"
sys.path.insert(0, str(FASTTASK_DIR))

S3_PORT = os.environ.get("TEST_S3_PORT", "10911")
CLUSTER_SECRET = "embedded-selftest-cluster-secret"
DATA_DIR = pathlib.Path(os.environ.get("TEST_S3_DATA_DIR", "/tmp/fasttask-s3-embedded"))
SUPERVISORD_CONF_DIR = FASTTASK_DIR / "supervisord_conf"

failures = []


def section(title):
    print(f"\n{'=' * 62}\n{title}\n{'=' * 62}")


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}{'' if cond else f'  -> {detail}'}")
    if not cond:
        failures.append(name)


def find_versitygw():
    explicit = os.environ.get("TEST_VERSITYGW_BIN")
    if explicit:
        return explicit
    found = shutil.which("versitygw")
    if found:
        return found
    # 兼容本地解压的 release 目录
    for candidate in pathlib.Path("/tmp").glob("**/versitygw"):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
    return None


VERSITYGW_BIN = find_versitygw()
if not VERSITYGW_BIN:
    print("❌ 找不到 versitygw 可执行文件")
    print("   请安装，或用 TEST_VERSITYGW_BIN 指定路径")
    sys.exit(1)
print(f"[env] versitygw    = {VERSITYGW_BIN}")
print(f"[env] s3 port      = {S3_PORT}")
print(f"[env] data dir     = {DATA_DIR}")

# --------------------------------------------------------------------------- #
section("1. start_s3.sh 按 RESULT_TYPE 决定是否启动")

env = os.environ.copy()
env.update(
    {
        "FASTTASK_DIR": str(FASTTASK_DIR),
        "RESULT_TYPE": "JSON",
        "TASK_QUEUE_PASSWD": CLUSTER_SECRET,
        "S3_PORT": S3_PORT,
        "S3_DATA_DIR": str(DATA_DIR),
        "S3_ACCESS_KEY": "",
        "S3_SECRET_KEY": "",
        "PATH": f"{pathlib.Path(VERSITYGW_BIN).parent}:{env.get('PATH', '')}",
    }
)

disabled = subprocess.run(
    ["bash", str(FASTTASK_DIR / "start_s3.sh")],
    env=env, capture_output=True, text=True, timeout=30,
)
check("RESULT_TYPE=JSON 时不启动服务", disabled.returncode == 0, disabled.stderr[-200:])
check("给出明确提示", "not started" in disabled.stdout, disabled.stdout)

# --------------------------------------------------------------------------- #
section("2. 启动服务（RESULT_TYPE=AUTO + 派生凭据）")

shutil.rmtree(DATA_DIR, ignore_errors=True)
env["RESULT_TYPE"] = "AUTO"

server_log_path = DATA_DIR.parent / "versitygw-selftest.log"
DATA_DIR.mkdir(parents=True, exist_ok=True)
server_log = open(str(server_log_path), "w")
server = subprocess.Popen(
    ["bash", str(FASTTASK_DIR / "start_s3.sh")],
    env=env, stdout=server_log, stderr=subprocess.STDOUT,
)


def wait_server(timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            connection = http.client.HTTPConnection("127.0.0.1", int(S3_PORT), timeout=2)
            connection.request("GET", "/")
            connection.getresponse()
            return True
        except Exception:  # noqa: BLE001
            time.sleep(0.5)
    return False


try:
    if not wait_server():
        print("  服务未就绪，日志：")
        print(server_log_path.read_text()[-1500:])
        sys.exit(1)
    check("服务已监听端口", True)

    # ----------------------------------------------------------------------- #
    section("3. 凭据派生一致性（bash 与 Python 必须算出同一个值）")

    shell_secret = subprocess.run(
        ["bash", "-c", f"printf 'fasttask:s3:%s' '{CLUSTER_SECRET}' | sha256sum | awk '{{print $1}}'"],
        capture_output=True, text=True, timeout=10,
    ).stdout.strip()
    python_secret = hashlib.sha256(
        f"fasttask:s3:{CLUSTER_SECRET}".encode("utf-8")
    ).hexdigest()
    check("密文完全一致", shell_secret == python_secret, f"{shell_secret} != {python_secret}")

    # ----------------------------------------------------------------------- #
    section("4. 用 Python 侧凭据读写（模拟 worker 上传 / master 预签名）")

    os.environ.update(
        {
            "NODE_TYPE": "distributed_worker",
            "MASTER_HOST": "127.0.0.1",
            "S3_PORT": S3_PORT,
            "S3_BUCKET": "embedded-selftest",
            "S3_SECURE": "False",
            "TASK_QUEUE_PASSWD": CLUSTER_SECRET,
            "S3_ACCESS_KEY": "",
            "S3_SECRET_KEY": "",
            "RESULT_TYPE": "AUTO",
            "RESULT_AUTO_TO_S3_SIZE": "1",
            "RESULT_EXPIRES": "3600",
            "RESULT_TO_S3_TRIES": "3",
        }
    )

    from utils import result_storage as rs  # noqa: E402

    access_key, secret_key = rs.derive_s3_credentials()
    check("派生凭据与启动脚本同源", access_key == "fasttask", access_key)

    rs.ensure_bucket()
    check("按派生凭据创建 bucket", rs.get_s3_client().bucket_exists("embedded-selftest"))

    payload = {"hello": "embedded", "size": 1024}
    stored = rs.finalize_task_result(payload, "embedded-selftest-1")
    check("结果成功外置", rs.detect_stored_result_type(stored) == rs.ResultType.s3, stored)

    reference = rs.build_s3_result_response(stored)
    check("生成预签名地址", bool(reference.get("url")), reference)
    check("url 是相对路径", reference["url"].startswith("/"), reference["url"][:60])
    # 本用例只起对象存储、没有 API 代理，因此直接拼内嵌服务地址访问
    with urllib.request.urlopen(
        f"http://127.0.0.1:{S3_PORT}{reference['url']}", timeout=15
    ) as response:
        body = response.read()
    check("预签名地址可下载", json.loads(body.decode("utf-8")) == payload, body[:120])

    # ----------------------------------------------------------------------- #
    section("5. supervisord 配置组装")

    def assemble(node_type, result_type, cleanup_enabled="False", flower_enabled="False"):
        shutil.rmtree(SUPERVISORD_CONF_DIR, ignore_errors=True)
        conf_env = {
            "NODE_TYPE": node_type,
            "RESULT_TYPE": result_type,
            "FILE_CLEANUP_ENABLED": cleanup_enabled,
            "FLOWER_ENABLED": flower_enabled,
        }
        original = {k: os.environ.get(k) for k in conf_env}
        os.environ.update(conf_env)
        try:
            cwd = os.getcwd()
            os.chdir(FASTTASK_DIR)
            try:
                import run as run_module

                run_module.assemble_supervisor_conf()
            finally:
                os.chdir(cwd)
        finally:
            for k, v in original.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        return sorted(p.name for p in SUPERVISORD_CONF_DIR.iterdir())

    conf = assemble("distributed_master", "AUTO")
    check("master + AUTO 启动 s3", "s3.conf" in conf, conf)
    check("master + AUTO 启动 uvicorn", "uvicorn.conf" in conf, conf)
    check("master 不启动 celery", "celery.conf" not in conf, conf)
    check("AUTO 模式下启用清理进程", "file_cleanup.conf" in conf, conf)

    conf = assemble("distributed_worker", "AUTO")
    check("worker 不启动 s3", "s3.conf" not in conf, conf)
    check("worker 启动 celery", "celery.conf" in conf, conf)
    check("worker 不启动 uvicorn", "uvicorn.conf" not in conf, conf)

    conf = assemble("single_node", "JSON", cleanup_enabled="True")
    check("JSON 模式不启动 s3", "s3.conf" not in conf, conf)
    check("JSON + 清理开启仍启动清理进程", "file_cleanup.conf" in conf, conf)

    conf = assemble("distributed_master", "S3", cleanup_enabled="False")
    check("S3 模式且清理关闭时仍启动清理进程", "file_cleanup.conf" in conf, conf)

finally:
    print("\n清理…")
    server.terminate()
    try:
        server.wait(timeout=10)
    except subprocess.TimeoutExpired:
        server.kill()
    server_log.close()
    shutil.rmtree(SUPERVISORD_CONF_DIR, ignore_errors=True)
    shutil.rmtree(DATA_DIR, ignore_errors=True)

print(f"\n{'=' * 62}")
if failures:
    print(f"❌ 失败 {len(failures)} 项：{failures}")
    print("=" * 62)
    sys.exit(1)
print("内嵌对象存储验收全部通过 ✅")
print("=" * 62)
