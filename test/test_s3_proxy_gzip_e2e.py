"""S3 代理传输压缩的真机验收测试（真实 versitygw + 真实 uvicorn）。

与 test_s3_proxy_gzip.py 的区别：那个用假上游 + TestClient 只验代理逻辑，
这个连真实对象存储与真实 ASGI server，验证在真实上游响应头（Content-Type /
Content-Length / ETag）与真实分块传输下的行为，并用"会自动解压的客户端"
（httpx，行为与 fasttask_manager 用的 requests 一致）确认对调用方透明。

依赖：
  - versitygw 可执行文件（在 PATH 中，或用 TEST_VERSITYGW_BIN 指定）
  - minio / httpx / uvicorn / starlette（即 fasttask/requirements.txt）

用法：
    python test/test_s3_proxy_gzip_e2e.py
"""

import gzip
import hashlib
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import time

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
FASTTASK_DIR = REPO_DIR / "fasttask"
WORK_DIR = pathlib.Path(os.environ.get("TEST_WORK_DIR", "/tmp/fasttask-s3-proxy-e2e"))
BUCKET = "proxy-compress-selftest"
ACCESS_KEY = "testkey"
SECRET_KEY = "testsecret"
MAX_BUFFER = 8 * 1024

failures = []


def section(title):
    print(f"\n{'=' * 62}\n{title}\n{'=' * 62}")


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}{'' if cond else f'  -> {detail}'}")
    if not cond:
        failures.append(name)


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_port(port, timeout=30):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket() as sock:
            sock.settimeout(0.5)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.2)
    return False


def find_versitygw():
    explicit = os.environ.get("TEST_VERSITYGW_BIN")
    if explicit:
        return explicit
    found = shutil.which("versitygw")
    if found:
        return found
    return None


APP_SOURCE = '''import os

from starlette.applications import Starlette

from utils.s3_proxy import S3ProxyMiddleware

app = Starlette()
# 只挂代理：这个 app 就是 fasttask 里 S3 那一段中间件栈的最小复现
app.add_middleware(
    S3ProxyMiddleware,
    bucket=os.environ["PROXY_TEST_BUCKET"],
    endpoint=os.environ["PROXY_TEST_S3_ENDPOINT"],
    compress=True,
    compresslevel=5,
    max_buffer=int(os.environ["PROXY_TEST_MAX_BUFFER"]),
)
'''


def build_app_env(s3_port, api_port):
    env = os.environ.copy()
    env["PROXY_TEST_BUCKET"] = BUCKET
    env["PROXY_TEST_S3_ENDPOINT"] = f"127.0.0.1:{s3_port}"
    env["PROXY_TEST_MAX_BUFFER"] = str(MAX_BUFFER)
    env["PYTHONPATH"] = str(FASTTASK_DIR)
    env["PYTHONUNBUFFERED"] = "1"
    return env


def start_versitygw(bin_path, s3_port, log_dir):
    buckets = WORK_DIR / "buckets"
    versions = WORK_DIR / "versions"
    buckets.mkdir(parents=True, exist_ok=True)
    versions.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["ROOT_ACCESS_KEY"] = ACCESS_KEY
    env["ROOT_SECRET_KEY"] = SECRET_KEY
    log = open(log_dir / "versitygw.log", "w")
    proc = subprocess.Popen(
        [
            bin_path,
            "--port",
            f":{s3_port}",
            "posix",
            "--versioning-dir",
            str(versions),
            str(buckets),
        ],
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    return proc, log


def start_uvicorn(s3_port, api_port, log_dir):
    (WORK_DIR / "app_proxy.py").write_text(APP_SOURCE)
    env = build_app_env(s3_port, api_port)
    log = open(log_dir / "uvicorn.log", "w")
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "app_proxy:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(api_port),
            "--log-level",
            "warning",
        ],
        cwd=WORK_DIR,
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
    )
    return proc, log


def to_proxy_url(presigned, api_port):
    """把预签名地址换成"走代理端口"的地址：只保留 path?query（Host 由客户端决定）。"""
    return f"http://127.0.0.1:{api_port}/" + presigned.split("/", 3)[3]


def main():
    bin_path = find_versitygw()
    if not bin_path:
        print("跳过：未找到 versitygw（用 TEST_VERSITYGW_BIN 指定路径）")
        return 0

    import httpx
    from minio import Minio

    shutil.rmtree(WORK_DIR, ignore_errors=True)
    log_dir = WORK_DIR / "log"
    log_dir.mkdir(parents=True, exist_ok=True)

    s3_port = free_port()
    api_port = free_port()
    procs = []
    logs = []
    try:
        proc, log = start_versitygw(bin_path, s3_port, log_dir)
        procs.append(proc)
        logs.append(log)
        proc, log = start_uvicorn(s3_port, api_port, log_dir)
        procs.append(proc)
        logs.append(log)

        if not wait_port(s3_port) or not wait_port(api_port):
            print("服务未就绪，查看日志：")
            for path in sorted(log_dir.glob("*.log")):
                print(f"--- {path}\n{path.read_text()[-2000:]}")
            return 1

        client = Minio(
            f"127.0.0.1:{s3_port}",
            access_key=ACCESS_KEY,
            secret_key=SECRET_KEY,
            secure=False,
        )
        if not client.bucket_exists(BUCKET):
            client.make_bucket(BUCKET)

        small = json.dumps({"rows": [{"i": i} for i in range(100)]}).encode()
        big = json.dumps([{"i": i, "v": "y" * 50} for i in range(20000)]).encode()
        objects = {"small": small, "big": big}

        from io import BytesIO

        for key, data in objects.items():
            client.put_object(
                BUCKET, key, BytesIO(data), length=len(data),
                content_type="application/json",
            )

        section("0. 上游（versitygw）原始响应头 —— 代理判定条件的依据")
        for key, data in objects.items():
            url = client.presigned_get_object(BUCKET, key)
            path_query = "/" + url.split("/", 3)[3]
            with httpx.Client(timeout=30) as c:
                r = c.get(f"http://127.0.0.1:{s3_port}{path_query}")
            check(f"{key}: 状态 200", r.status_code == 200, r.status_code)
            check(
                f"{key}: Content-Type 是 application/json",
                r.headers.get("content-type", "").startswith("application/json"),
                r.headers.get("content-type"),
            )
            check(
                f"{key}: 带 Content-Length",
                r.headers.get("content-length") == str(len(data)),
                r.headers.get("content-length"),
            )
            check(f"{key}: 有 ETag", bool(r.headers.get("etag")), r.headers)

        section("0.5 经代理透传大对象（identity）→ 必须完整（防 chunked 请求体截断回归）")
        presigned = client.presigned_get_object(BUCKET, "big")
        passthrough_url = to_proxy_url(presigned, api_port)
        with httpx.Client(timeout=60) as c:
            with c.stream(
                "GET", passthrough_url, headers={"Accept-Encoding": "identity"}
            ) as r:
                raw = b"".join(r.iter_raw())
                check("状态 200", r.status_code == 200, r.status_code)
                check("无 Content-Encoding", "content-encoding" not in r.headers)
                check(
                    "完整收完（不截断）",
                    len(raw) == len(big) and raw == big,
                    (len(raw), len(big)),
                )

        section("1. 大对象（> max_buffer）→ 流式压缩，客户端自动解压透明")
        presigned = client.presigned_get_object(BUCKET, "big")
        proxy_url = to_proxy_url(presigned, api_port)
        with httpx.Client(timeout=60) as c:
            with c.stream("GET", proxy_url, headers={"Accept-Encoding": "gzip"}) as r:
                raw = b"".join(r.iter_raw())
                check("状态 200", r.status_code == 200, r.status_code)
                check(
                    "Content-Encoding: gzip",
                    r.headers.get("content-encoding") == "gzip",
                    dict(r.headers),
                )
                check(
                    "无 Content-Length（真实分块传输）",
                    "content-length" not in r.headers,
                    r.headers.get("content-length"),
                )
                check("裸字节确实是 gzip", raw[:2] == b"\x1f\x8b", raw[:8])
                check("解压后与原文一致", gzip.decompress(raw) == big)
                check(
                    f"压缩率 {len(raw) / len(big):.1%}",
                    len(raw) < len(big) // 5,
                    (len(raw), len(big)),
                )
                check(
                    "弱化 ETag",
                    (r.headers.get("etag") or "").startswith("W/"),
                    r.headers.get("etag"),
                )
                check(
                    "剔除校验头",
                    "content-md5" not in r.headers
                    and not any(k.startswith("x-amz-checksum-") for k in r.headers),
                    dict(r.headers),
                )

        # fasttask_manager 的行为：不做任何特殊处理，直接取解压后的内容
        with httpx.Client(timeout=60) as c:
            r = c.get(proxy_url, headers={"Accept-Encoding": "gzip, deflate"})
            check("客户端默认路径拿到的就是原文", r.content == big)
            check(
                "sha256 与对象一致",
                hashlib.sha256(r.content).hexdigest()
                == hashlib.sha256(big).hexdigest(),
            )
            check(
                "size_bytes 语义未变",
                len(r.content) == len(big),
                (len(r.content), len(big)),
            )

        section("1.5 requests 同款解码路径（urllib3 decode_content）同样透明")
        # fasttask_manager 用 requests：r.content / iter_content 底层就是
        # urllib3 的 read(decode_content=True)，这里直接验那条路径
        import urllib3

        http = urllib3.PoolManager()
        r = http.request(
            "GET",
            proxy_url,
            headers={"Accept-Encoding": "gzip"},
            preload_content=False,
        )
        decoded = r.read(decode_content=True)
        check("解压后与原文一致", decoded == big)
        check(
            "sha256 与对象一致",
            hashlib.sha256(decoded).hexdigest() == hashlib.sha256(big).hexdigest(),
        )
        check(
            "响应确实声明了 gzip（客户端无感）",
            r.headers.get("content-encoding") == "gzip",
            r.headers.get("content-encoding"),
        )

        section("2. 小对象（< max_buffer）→ 整块压缩，保留 Content-Length")
        presigned = client.presigned_get_object(BUCKET, "small")
        proxy_url = to_proxy_url(presigned, api_port)
        with httpx.Client(timeout=30) as c:
            with c.stream("GET", proxy_url, headers={"Accept-Encoding": "gzip"}) as r:
                raw = b"".join(r.iter_raw())
                check(
                    "Content-Encoding: gzip",
                    r.headers.get("content-encoding") == "gzip",
                    dict(r.headers),
                )
                check(
                    "Content-Length 为压缩后长度",
                    r.headers.get("content-length") == str(len(raw)),
                    (r.headers.get("content-length"), len(raw)),
                )
                check("解压后与原文一致", gzip.decompress(raw) == small)

        section("3. 未声明 gzip → 原样透传（向后兼容）")
        with httpx.Client(timeout=30) as c:
            with c.stream(
                "GET", proxy_url, headers={"Accept-Encoding": "identity"}
            ) as r:
                raw = b"".join(r.iter_raw())
                check("无 Content-Encoding", "content-encoding" not in r.headers)
                check(
                    "Content-Length 即原文长度",
                    r.headers.get("content-length") == str(len(small)),
                )
                check("body 即原文", raw == small)

        section("4. Range 请求 → 不压，保住 206 的字节语义")
        with httpx.Client(timeout=30) as c:
            with c.stream(
                "GET",
                proxy_url,
                headers={"Accept-Encoding": "gzip", "Range": "bytes=0-9"},
            ) as r:
                raw = b"".join(r.iter_raw())
                check("状态 206", r.status_code == 206, r.status_code)
                check("无 Content-Encoding", "content-encoding" not in r.headers)
                check("返回 10 字节原始片段", raw == small[:10], raw[:20])
                check("Content-Range 保留", "content-range" in r.headers)
    except Exception:
        import traceback

        traceback.print_exc()
        failures.append("未捕获异常（见上方 traceback）")
    finally:
        for proc in procs:
            proc.terminate()
        for proc in procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        for log in logs:
            log.close()
        # 失败时日志是唯一线索，直接 dump 出来
        for path in sorted(log_dir.glob("*.log")):
            content = path.read_text()[-3000:]
            print(f"--- {path}\n{content}")

    print(f"\n{'=' * 62}")
    if failures:
        print(f"❌ {len(failures)} 项失败：{failures}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
