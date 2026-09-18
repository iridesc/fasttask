"""对象存储透明代理：让内嵌的 S3 服务复用 API 的端口对外提供服务。

设计要点
--------
- **对外只暴露一个端口**：客户端拿到的是相对路径（``/<bucket>/<key>?X-Amz-...``），
  拼上自己访问 FastTask 的地址即可下载，不需要给对象存储单独开端口。
- **Host 必须重写**：预签名地址是以服务端内部的 ``S3_ENDPOINT``
  （如 ``127.0.0.1:9000``）作为 Host 计算的，而客户端用的是自己的地址。
  这里转发时统一把 Host 换回内部地址，否则 SigV4 校验必然失败
  （403 SignatureDoesNotMatch）。
- **路径与 query 原样透传**：SigV4 的 canonical URI 含路径与查询参数，不能改动。
- 只转发 ``/{bucket}/`` 前缀的请求，因此不会与 ``/docs``、``/check`` 等路由冲突。
"""

import httpx


class S3ProxyMiddleware:
    def __init__(self, app, bucket, endpoint):
        self.app = app
        self.prefix = f"/{bucket}/"
        # 转发目标与 Host 重写值取同一个地址（即 S3_ENDPOINT）：
        # 预签名就是按它计算的 Host，两者必须一致。
        self.target = f"http://{endpoint}"
        self.upstream_host = endpoint

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

        client = httpx.AsyncClient(timeout=None, trust_env=False)
        try:
            request = client.build_request(
                scope["method"],
                url,
                headers=headers,
                content=self._request_body(receive),
            )
            response = await client.send(request, stream=True)

            await send(
                {
                    "type": "http.response.start",
                    "status": response.status_code,
                    "headers": [
                        (name.lower().encode("latin-1"), value.encode("latin-1"))
                        for name, value in response.headers.items()
                    ],
                }
            )
            # aiter_raw：原样透传字节（不自动解压/解码）
            async for chunk in response.aiter_raw():
                await send(
                    {"type": "http.response.body", "body": chunk, "more_body": True}
                )
            await send({"type": "http.response.body", "body": b"", "more_body": False})
        finally:
            await client.aclose()

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
