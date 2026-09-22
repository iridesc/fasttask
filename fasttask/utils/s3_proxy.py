"""对象存储透明代理：让内嵌的 S3 服务复用 API 的端口对外提供服务。

设计要点
--------
- **对外只暴露一个端口**：客户端拿到的是相对路径（``/<bucket>/<key>?X-Amz-...``），
  拼上自己访问 FastTask 的地址即可下载，不需要给对象存储单独开端口。
- **Host 必须重写**：预签名地址是以服务端内部的对象存储地址
  （如 ``127.0.0.1:9000``）作为 Host 计算的，而客户端用的是自己的地址。
  这里转发时统一把 Host 换回内部地址，否则 SigV4 校验必然失败
  （403 SignatureDoesNotMatch）。
- **路径与 query 原样透传**：SigV4 的 canonical URI 含路径与查询参数，不能改动。
- 只转发 ``/{bucket}/`` 前缀的请求，因此不会与 ``/docs``、``/check`` 等路由冲突。
- **可选的传输压缩**（``RESPONSE_COMPRESS``）：客户端声明
  ``Accept-Encoding: gzip`` 时把响应压成 gzip 再发，结果 JSON 通常只剩 12%~16%。
  压缩放在这里而不是通用 gzip 中间件里，是因为 S3 请求在本中间件（最外层）就被
  接管转发了，根本进不到内层的中间件。压缩在线程池中执行，不阻塞事件循环。
"""

import asyncio
import gzip
import zlib

import httpx

#: 小于该字节数的对象不值得压（与 SelectiveGZipMiddleware 的 minimum_size 一致）
_MIN_COMPRESS_SIZE = 1000

#: 压缩后与实体不再匹配、必须剔除的响应头（客户端可能拿它们校验压缩后的字节）
_STRIP_ON_COMPRESS = ("content-md5",)
_STRIP_PREFIX_ON_COMPRESS = ("x-amz-checksum-",)

#: zlib 的 gzip 包装（16 + 15，即 gzip 容器而非裸 deflate）
_GZIP_WBITS = 16 + zlib.MAX_WBITS

#: 可能带请求体的方法：其余方法（GET/HEAD/DELETE/OPTIONS）不挂 body 流。
#: 这是个真实踩过的坑：GET 一旦挂上 ASGI body 流，httpx 会用
#: ``Transfer-Encoding: chunked`` 发一个空请求体，versitygw 在该情况下会于大响应
#: 传输中途关闭连接（实测下载 1.4MB 对象时在 80KB 处被截断），必须避免。
_METHODS_WITH_BODY = ("POST", "PUT", "PATCH")


class S3ProxyMiddleware:
    def __init__(
        self,
        app,
        bucket,
        endpoint,
        compress=False,
        compresslevel=5,
        max_buffer=16 * 1024 * 1024,
    ):
        self.app = app
        self.prefix = f"/{bucket}/"
        # 转发目标与 Host 重写值取同一个地址（即内部对象存储地址）：
        # 预签名就是按它计算的 Host，两者必须一致。
        self.target = f"http://{endpoint}"
        self.upstream_host = endpoint
        # 传输压缩：小对象整块压（能写回 Content-Length），大对象边收边压
        self.compress = compress
        self.compresslevel = compresslevel
        self.max_buffer = max_buffer

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith(self.prefix):
            await self.app(scope, receive, send)
            return
        await self._forward(scope, receive, send)

    async def _forward(self, scope, receive, send):
        query = scope["query_string"].decode()
        url = f"{self.target}{scope['path']}"
        if query:
            url = f"{url}?{query}"

        headers = {k.decode(): v.decode() for k, v in scope["headers"]}
        headers["host"] = self.upstream_host
        request_headers = {k.lower(): v for k, v in headers.items()}

        client = httpx.AsyncClient(timeout=None, trust_env=False)
        try:
            request = client.build_request(
                scope["method"],
                url,
                headers=headers,
                content=(
                    self._request_body(receive)
                    if scope["method"] in _METHODS_WITH_BODY
                    else None
                ),
            )
            response = await client.send(request, stream=True)

            mode = self._decide_mode(scope["method"], request_headers, response)
            if mode == "buffered":
                await self._buffered_gzip(response, send)
            elif mode == "stream":
                await self._stream_gzip(response, send)
            else:
                await self._passthrough(response, send)
        finally:
            await client.aclose()

    # ------------------------------------------------------------------ #
    # 响应下发方式
    # ------------------------------------------------------------------ #
    def _decide_mode(self, method, request_headers, response):
        """决定响应下发方式：``passthrough`` / ``buffered`` / ``stream``。

        只在"压缩有意义且不破坏语义"时才压，下面每条排除项都对应一种会出错的情况。
        """
        if not self.compress:
            return "passthrough"
        if "gzip" not in request_headers.get("accept-encoding", "").lower():
            # 客户端没声明支持：压了就是单方面破坏兼容
            return "passthrough"
        if "range" in request_headers:
            # Range 响应是 206 + Content-Range 的字节片段，压缩会让偏移语义失效
            return "passthrough"
        if method != "GET":
            # HEAD 没有响应体
            return "passthrough"
        if response.status_code != 200:
            return "passthrough"
        if response.headers.get("content-encoding"):
            # 上游已经压过，避免二次压缩
            return "passthrough"
        content_type = response.headers.get("content-type", "")
        if content_type.split(";")[0].strip().lower() != "application/json":
            # 只压已知熵不高的结果对象：其他类型（gzip/zip 等已压缩格式）压了可能
            # 反而变大，而流式压缩发出前无法反悔
            return "passthrough"
        length = response.headers.get("content-length", "")
        if not length.isdigit() or int(length) < _MIN_COMPRESS_SIZE:
            return "passthrough"
        return "buffered" if int(length) <= self.max_buffer else "stream"

    async def _passthrough(self, response, send):
        await send(
            {
                "type": "http.response.start",
                "status": response.status_code,
                "headers": self._encode_headers(response.headers.items()),
            }
        )
        # aiter_raw：原样透传字节（不自动解压/解码）
        async for chunk in response.aiter_raw():
            await send({"type": "http.response.body", "body": chunk, "more_body": True})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    async def _buffered_gzip(self, response, send):
        """小对象：攒成完整 body 再压，能写回精确的 Content-Length。"""
        body = await response.aread()
        # 关键：压缩在线程池里做，zlib 压缩期间释放 GIL，不阻塞事件循环
        compressed = await asyncio.to_thread(gzip.compress, body, self.compresslevel)

        if len(compressed) >= len(body):
            # 没压小（JSON 上几乎不会发生）：别白搭一层编码
            await send(
                {
                    "type": "http.response.start",
                    "status": response.status_code,
                    "headers": self._encode_headers(response.headers.items()),
                }
            )
            await send({"type": "http.response.body", "body": body})
            return

        await send(
            {
                "type": "http.response.start",
                "status": response.status_code,
                "headers": self._compressed_headers(response, len(compressed)),
            }
        )
        await send({"type": "http.response.body", "body": compressed})

    async def _stream_gzip(self, response, send):
        """大对象：边收边压（chunked，无 Content-Length），内存只占一个 chunk。"""
        await send(
            {
                "type": "http.response.start",
                "status": response.status_code,
                "headers": self._compressed_headers(response, length=None),
            }
        )
        compressor = zlib.compressobj(self.compresslevel, zlib.DEFLATED, _GZIP_WBITS)
        async for chunk in response.aiter_raw():
            out = await asyncio.to_thread(compressor.compress, chunk)
            if out:
                await send(
                    {"type": "http.response.body", "body": out, "more_body": True}
                )
        # flush 必须发出：gzip 尾部（CRC32 + ISIZE）缺失时客户端会判定流被截断
        tail = await asyncio.to_thread(compressor.flush)
        await send({"type": "http.response.body", "body": tail, "more_body": False})

    # ------------------------------------------------------------------ #
    # 响应头
    # ------------------------------------------------------------------ #
    def _compressed_headers(self, response, length):
        """压缩响应的头：剔除与压缩后字节不符的校验头，补 Content-Encoding 与 Vary。"""
        headers = []
        for name, value in response.headers.items():
            lower = name.lower()
            if lower == "content-length":
                # 长度由压缩结果决定，统一在下面重算
                continue
            if lower in _STRIP_ON_COMPRESS or lower.startswith(
                _STRIP_PREFIX_ON_COMPRESS
            ):
                continue
            if lower == "etag" and not value.startswith("W/"):
                # 实体内容变了，弱化 ETag（与 nginx gzip 的处理一致）
                value = f"W/{value}"
            headers.append((lower, value))

        headers.append(("content-encoding", "gzip"))
        if length is not None:
            headers.append(("content-length", str(length)))
        self._add_vary(headers, "Accept-Encoding")
        return self._encode_headers(headers)

    @staticmethod
    def _add_vary(headers, field):
        """把 field 合进已有的 Vary 头（没有则新增），避免出现重复的 Vary。"""
        for index, (name, value) in enumerate(headers):
            if name == "vary":
                if field.lower() not in value.lower():
                    headers[index] = ("vary", f"{value}, {field}")
                return headers
        headers.append(("vary", field))
        return headers

    @staticmethod
    def _encode_headers(items):
        return [
            (name.lower().encode("latin-1"), value.encode("latin-1"))
            for name, value in items
        ]

    @staticmethod
    async def _request_body(receive):
        """把 ASGI 请求体转成可迭代对象（上传大对象时分块读取，不整块驻留内存）。"""
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            body = message.get("body", b"")
            if body:
                yield body
            if not message.get("more_body", False):
                return
