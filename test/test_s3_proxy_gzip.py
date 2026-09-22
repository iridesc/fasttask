"""S3 代理传输压缩（跟随 ``RESPONSE_COMPRESS`` 一套参数）验收测试。

只验 ``S3ProxyMiddleware`` 这一层：用一个假上游（http.server）冒充 versitygw，
再用 Starlette TestClient 直接请求代理，断言各种情况下的响应形态
（是否压缩、是否是流式、校验头是否被剔除、Range 是否被放过）。

用法：
    python test/test_s3_proxy_gzip.py
"""

import gzip
import http.server
import json
import pathlib
import sys
import threading

REPO_DIR = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_DIR / "fasttask"))

from starlette.applications import Starlette  # noqa: E402
from starlette.testclient import TestClient  # noqa: E402

from utils.s3_proxy import S3ProxyMiddleware  # noqa: E402

#: 小于 _MIN_COMPRESS_SIZE(1000)，不该被压
SMALL_BODY = json.dumps({"hello": "world"}).encode()
#: 1000 < size < max_buffer(8KB)：整块压缩，保留 Content-Length
MEDIUM_BODY = json.dumps([{"i": i, "v": "x" * 20} for i in range(150)]).encode()
#: > max_buffer：改为边收边压（chunked，无 Content-Length）
BIG_BODY = json.dumps([{"i": i, "v": "y" * 50} for i in range(20000)]).encode()
#: 上游自带 Content-Encoding 的内容（模拟已压缩对象）：不能再压一次
PRE_COMPRESSED = gzip.compress(MEDIUM_BODY)

BODIES = {
    "small": SMALL_BODY,
    "medium": MEDIUM_BODY,
    "big": BIG_BODY,
    "precompressed": PRE_COMPRESSED,
}

MAX_BUFFER = 8 * 1024
failures = []


def read_raw(response):
    """读未解压的原始字节（httpx 的 read()/iter_bytes() 会自动解压，这里要看到真身）。"""
    return b"".join(response.iter_raw())


def section(title):
    print(f"\n{'=' * 62}\n{title}\n{'=' * 62}")


def check(name, cond, detail=""):
    print(f"  {'✅' if cond else '❌'} {name}{'' if cond else f'  -> {detail}'}")
    if not cond:
        failures.append(name)


class FakeS3Handler(http.server.BaseHTTPRequestHandler):
    """最小 S3 语义：200 全量、206 Range、带 ETag/校验头。"""

    protocol_version = "HTTP/1.1"

    def _key(self):
        return self.path.split("?")[0].rsplit("/", 1)[-1]

    def _respond(self, status, body, extra=None, with_check_headers=True):
        self.send_response(status)
        headers = {"Content-Type": "application/json"}
        if with_check_headers:
            headers["ETag"] = '"d41d8cd98f00b204e9800998ecf8427e"'
            headers["Content-MD5"] = "rL0Y20zC+Fzt72VPzMSk2A=="
            headers["x-amz-checksum-crc32"] = "AAAAAA=="
        headers.update(extra or {})
        headers["Content-Length"] = str(len(body))
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_GET(self):
        key = self._key()
        body = BODIES.get(key, MEDIUM_BODY)
        pre_compressed = key == "precompressed"
        if pre_compressed:
            body = PRE_COMPRESSED

        range_header = self.headers.get("Range")
        if range_header:
            start, end = range_header.split("=", 1)[1].split("-", 1)
            start, end = int(start), int(end)
            part = body[start : end + 1]
            self._respond(
                206,
                part,
                extra={"Content-Range": f"bytes {start}-{end}/{len(body)}"},
            )
            return

        extra = {"Content-Encoding": "gzip"} if pre_compressed else None
        self._respond(200, body, extra=extra)

    def do_HEAD(self):
        self._respond(200, BODIES.get(self._key(), MEDIUM_BODY))

    def log_message(self, *args):
        pass


class FakeS3:
    def __init__(self):
        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), FakeS3Handler)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def endpoint(self):
        return f"127.0.0.1:{self.port}"

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def build_client(upstream, **kwargs):
    app = Starlette()
    app.add_middleware(
        S3ProxyMiddleware,
        bucket="b",
        endpoint=upstream.endpoint,
        compress=kwargs.pop("compress", True),
        compresslevel=5,
        max_buffer=MAX_BUFFER,
        **kwargs,
    )
    return TestClient(app)


def main():
    upstream = FakeS3()
    # TestClient 用 httpx，默认会解压响应；这里统一用 stream 模式发请求，
    # 以便看到 gzip 原始字节和未改写的响应头。
    client = build_client(upstream)

    try:
        section("1. 声明 gzip + 中等对象 -> 整块压缩（保留 Content-Length）")
        with client.stream(
            "GET", "/b/medium", headers={"Accept-Encoding": "gzip"}
        ) as r:
            raw = read_raw(r)
            check("状态 200", r.status_code == 200, r.status_code)
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
            check("确实变小了", len(raw) < len(MEDIUM_BODY), (len(raw), len(MEDIUM_BODY)))
            check("解压后与原文一致", gzip.decompress(raw) == MEDIUM_BODY)
            check(
                "补了 Vary: Accept-Encoding",
                "accept-encoding" in (r.headers.get("vary") or "").lower(),
                r.headers.get("vary"),
            )
            check(
                "ETag 被弱化",
                (r.headers.get("etag") or "").startswith("W/"),
                r.headers.get("etag"),
            )
            check("剔除 Content-MD5", "content-md5" not in r.headers, dict(r.headers))
            check(
                "剔除 x-amz-checksum-*",
                "x-amz-checksum-crc32" not in r.headers,
                dict(r.headers),
            )

        section("2. 未声明 gzip -> 原样透传（向后兼容）")
        with client.stream(
            "GET", "/b/medium", headers={"Accept-Encoding": "identity"}
        ) as r:
            raw = read_raw(r)
            check("无 Content-Encoding", "content-encoding" not in r.headers)
            check("未改动 ETag", not (r.headers.get("etag") or "").startswith("W/"))
            check("保留 Content-MD5", "content-md5" in r.headers)
            check("body 即原文", raw == MEDIUM_BODY)
            check(
                "Content-Length 即原文长度",
                r.headers.get("content-length") == str(len(MEDIUM_BODY)),
            )

        section("3. 小对象（< 1000 字节）-> 不压")
        with client.stream(
            "GET", "/b/small", headers={"Accept-Encoding": "gzip"}
        ) as r:
            raw = read_raw(r)
            check("无 Content-Encoding", "content-encoding" not in r.headers)
            check("body 即原文", raw == SMALL_BODY)

        section("4. 大对象（> max_buffer）-> 流式压缩（chunked，无 Content-Length）")
        with client.stream("GET", "/b/big", headers={"Accept-Encoding": "gzip"}) as r:
            raw = read_raw(r)
            check("状态 200", r.status_code == 200)
            check(
                "Content-Encoding: gzip",
                r.headers.get("content-encoding") == "gzip",
                dict(r.headers),
            )
            check(
                "无 Content-Length（改分块传输）",
                "content-length" not in r.headers,
                r.headers.get("content-length"),
            )
            check("压缩率显著", len(raw) < len(BIG_BODY) // 5, (len(raw), len(BIG_BODY)))
            check("解压后与原文一致", gzip.decompress(raw) == BIG_BODY)

        section("5. Range 请求 -> 不压（保住 206 的字节语义）")
        with client.stream(
            "GET",
            "/b/medium",
            headers={"Accept-Encoding": "gzip", "Range": "bytes=0-9"},
        ) as r:
            raw = read_raw(r)
            check("状态 206", r.status_code == 206, r.status_code)
            check("无 Content-Encoding", "content-encoding" not in r.headers)
            check("返回 10 字节原始片段", raw == MEDIUM_BODY[:10], raw[:20])
            check("Content-Range 保留", "content-range" in r.headers)

        section("6. HEAD 请求 -> 不压")
        with client.stream(
            "HEAD", "/b/medium", headers={"Accept-Encoding": "gzip"}
        ) as r:
            check("无 Content-Encoding", "content-encoding" not in r.headers)

        section("7. 上游已带 Content-Encoding -> 不二次压缩")
        with client.stream(
            "GET", "/b/precompressed", headers={"Accept-Encoding": "gzip"}
        ) as r:
            raw = read_raw(r)
            check(
                "沿用上游的 Content-Encoding",
                r.headers.get("content-encoding") == "gzip",
                dict(r.headers),
            )
            check(
                "Content-Length 即上游长度（未被重算）",
                r.headers.get("content-length") == str(len(PRE_COMPRESSED)),
            )
            check("未插手加 Vary", "vary" not in r.headers, r.headers.get("vary"))
            check("body 未被再压一层", gzip.decompress(raw) == MEDIUM_BODY)

        section("8. 开关关闭 -> 即使声明 gzip 也不压")
        plain_client = build_client(upstream, compress=False)
        with plain_client.stream(
            "GET", "/b/medium", headers={"Accept-Encoding": "gzip"}
        ) as r:
            check("无 Content-Encoding", "content-encoding" not in r.headers)
            check("body 即原文", read_raw(r) == MEDIUM_BODY)

        section("9. query 原样透传（SigV4 前提）")
        with client.stream(
            "GET",
            "/b/medium?X-Amz-Signature=abc&X-Amz-Date=20240101T000000Z",
            headers={"Accept-Encoding": "identity"},
        ) as r:
            check("状态 200", r.status_code == 200, r.status_code)
            check("body 可下载", read_raw(r) == MEDIUM_BODY)
    finally:
        client.close()
        upstream.close()

    print(f"\n{'=' * 62}")
    if failures:
        print(f"❌ {len(failures)} 项失败：{failures}")
        return 1
    print("✅ 全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
