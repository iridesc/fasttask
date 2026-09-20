"""请求上下文：让深层代码拿到当前请求的对外地址。

为什么需要它
------------
外置结果的预签名 ``url`` 原本只返回相对路径，由调用方自己拼服务地址。但调用方
（尤其是 AI 客户端）通常并不知道自己被配置成了什么地址 —— 实测中它为了找这个
地址翻了多次配置文件。所以由服务端直接给出**可直接访问的完整 URL** 更合理。

而生成预签名发生在 ``utils/result_storage.py`` 这种深层代码里，拿不到 FastAPI 的
Request 对象，因此用 contextvar 把「当前请求的 scheme://host」透传下去。

拿不到上下文时（非 HTTP 调用路径）返回 None，调用方退回相对路径。
"""

import contextvars

_request_base_url = contextvars.ContextVar("fasttask_request_base_url", default=None)


def set_request_base_url(base_url):
    """记录当前请求的对外地址，返回用于复原的 token。"""
    return _request_base_url.set(base_url)


def reset_request_base_url(token):
    """复原到记录前的值，避免污染同一 worker 里的后续请求。"""
    _request_base_url.reset(token)


def get_request_base_url():
    """当前请求的 ``scheme://host``；非 HTTP 上下文时为 None。"""
    return _request_base_url.get()
