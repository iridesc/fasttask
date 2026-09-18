"""结果存储层：校验、规范化、按需把大结果搬到对象存储。

设计要点
--------
- 校验前移：任务返回时就用 ``Result`` 模型校验（fail-fast），
  写入 Redis / 对象存储的数据一定是合法的（历史上校验发生在 check 时，
  任务早就跑完、问题暴露太晚）。
- 结果去向由 ``RESULT_TYPE`` 决定：

  ==========  ====================================================
  JSON        结果内联进 Celery backend（默认，行为与历史一致）
  S3          结果一律上传对象存储，Redis 只留引用
  AUTO        序列化后超过 ``RESULT_AUTO_TO_S3_SIZE`` 才走对象存储
  ==========  ====================================================

- S3 凭据复用集群信任根 ``TASK_QUEUE_PASSWD`` 派生，master 与所有 worker
  各自算出同一份，部署方无需新增配置；可用 ``S3_ACCESS_KEY`` /
  ``S3_SECRET_KEY`` 显式覆盖。
- 对象 key 带日期前缀 ``{prefix}/{YYYYMMDD}/{result_id}.json``，
  便于清理进程按前缀定位，也方便人工排查。
- 上传发生在 worker 侧（任务返回时），预签名发生在 master 侧（check 时）。
"""

import hashlib
import io
import json
import os
from datetime import datetime, timedelta, timezone
from enum import Enum
from urllib.parse import urlparse

from retry import retry


class ResultStorageMode(Enum):
    """``RESULT_TYPE`` 的配置取值（决定结果去向）。"""

    json = "JSON"  # 内联在 Celery backend（历史行为）
    s3 = "S3"  # 一律外置到对象存储
    auto = "AUTO"  # 超过阈值才外置


class ResultType(Enum):
    """结果的形态（出现在 /check 响应与存储层 payload 里）。"""

    json = "json"  # result 即结果本身
    s3 = "s3"  # result 是对象存储引用
    text = "text"  # result 是错误信息或状态文本


#: RESULT_TYPE 的合法配置值。非法值一律直接报错，
#: 静默当成 JSON 会让“以为开了外置、实际没开”这类问题极难排查。
VALID_RESULT_TYPES = tuple(mode.value for mode in ResultStorageMode)

#: 存储层标记：结果 payload 顶层出现该字段即表示“内容已搬到对象存储”。
#: 不能用任务自己的 Result 字段做判断（会与业务字段冲突），所以用框架私有键。
STORAGE_MARKER = "__fasttask_storage__"

_CONTENT_TYPE_JSON = "application/json"

_s3_client = None
_s3_client_endpoint = None


# --------------------------------------------------------------------------- #
# 配置读取
# --------------------------------------------------------------------------- #
def get_configured_storage_mode():
    """RESULT_TYPE 的配置取值，默认 ``JSON``。

    非法取值直接报错（防御层：启动时 run.py 已经校验过一次）。
    """
    value = os.environ.get("RESULT_TYPE", ResultStorageMode.json.value).strip().upper()
    try:
        return ResultStorageMode(value)
    except ValueError:
        raise RuntimeError(
            f"RESULT_TYPE must be one of {VALID_RESULT_TYPES}, got {value!r}"
        ) from None


def is_s3_enabled():
    """是否需要对象存储参与（S3 与 AUTO 模式都需要）。"""
    return get_configured_storage_mode() in (
        ResultStorageMode.s3,
        ResultStorageMode.auto,
    )


# 以下读取的配置项默认值统一在 run.py 的 Env 声明里定义（单一来源），
# 这里直接取，缺什么就报什么，不再重复维护一份默认值。
def get_auto_offload_size():
    return int(os.environ["RESULT_AUTO_TO_S3_SIZE"])


def get_presign_expires():
    return int(os.environ["S3_PRESIGN_EXPIRES"])


def get_bucket():
    return os.environ["S3_BUCKET"]


def get_s3_port():
    return os.environ["S3_PORT"]


def get_s3_endpoint():
    """对象存储地址（服务端连接用）。

    未显式配置 `S3_ENDPOINT` 时按内嵌对象存储推导：
    master / single_node 走本机，worker 走 MASTER_HOST。
    """
    endpoint = (os.environ.get("S3_ENDPOINT") or "").strip()
    if endpoint:
        return endpoint

    if os.environ.get("NODE_TYPE") in ("single_node", "distributed_master"):
        return f"127.0.0.1:{get_s3_port()}"
    return f"{os.environ.get('MASTER_HOST', '127.0.0.1')}:{get_s3_port()}"


def get_object_prefix():
    return os.environ.get("S3_PREFIX", "").strip("/")


def derive_s3_credentials():
    """S3 凭据：显式配置优先，否则由集群信任根派生。

    派生保证 master 与所有 worker 得到完全一致的凭据，
    因此部署方只需要配置既有的 ``TASK_QUEUE_PASSWD``。
    """
    access_key = (os.environ.get("S3_ACCESS_KEY") or "").strip()
    secret_key = (os.environ.get("S3_SECRET_KEY") or "").strip()
    if access_key and secret_key:
        return access_key, secret_key

    cluster_secret = os.environ.get("TASK_QUEUE_PASSWD", "")
    digest = hashlib.sha256(
        f"fasttask:s3:{cluster_secret}".encode("utf-8")
    ).hexdigest()
    return "fasttask", digest


def _build_client(endpoint, secure):
    """构造 minio 客户端（延迟导入：RESULT_TYPE=JSON 时不引入该依赖）。"""
    from minio import Minio

    access_key, secret_key = derive_s3_credentials()
    return Minio(
        endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=secure,
        region=os.environ.get("S3_REGION") or None,
        cert_check=os.environ.get("S3_VERIFY_SSL", "True") == "True",
    )


def get_s3_client():
    """服务端连接用客户端（内嵌对象存储地址）。

    预签名地址也用它计算：Host 固定为内嵌地址，由代理在转发时统一重写，
    因此下载方用什么地址访问都不影响验签。
    """
    global _s3_client, _s3_client_endpoint

    endpoint = get_s3_endpoint()
    if _s3_client is None or _s3_client_endpoint != endpoint:
        _s3_client = _build_client(
            endpoint, os.environ.get("S3_SECURE", "False") == "True"
        )
        _s3_client_endpoint = endpoint
    return _s3_client


# --------------------------------------------------------------------------- #
# key 与阈值
# --------------------------------------------------------------------------- #
def build_object_key(result_id, at=None):
    """对象 key：``{prefix}/{YYYYMMDD}/{result_id}.json``。"""
    date_part = (at or datetime.now(timezone.utc)).strftime("%Y%m%d")
    return "/".join(
        part
        for part in (get_object_prefix(), date_part, f"{result_id}.json")
        if part
    )


def should_offload(size_bytes):
    mode = get_configured_storage_mode()
    if mode is ResultStorageMode.s3:
        return True
    if mode is ResultStorageMode.auto:
        return size_bytes > get_auto_offload_size()
    return False


# --------------------------------------------------------------------------- #
# 存储层 payload
# --------------------------------------------------------------------------- #
def make_storage_payload(result_id, data):
    """构造写入 Celery backend 的引用对象（内容已上传对象存储）。"""
    key = build_object_key(result_id)
    return {
        STORAGE_MARKER: ResultType.s3.value,
        "key": key,
        "uri": f"s3://{get_bucket()}/{key}",
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def detect_stored_result_type(raw):
    """判断存储层里的结果类型（历史数据没有标记，视为 json）。"""
    if isinstance(raw, dict) and raw.get(STORAGE_MARKER) == ResultType.s3.value:
        return ResultType.s3
    return ResultType.json


# --------------------------------------------------------------------------- #
# 上传（worker 侧）
# --------------------------------------------------------------------------- #
def put_result_object(client, bucket, key, data):
    """上传结果对象；失败按 RESULT_TO_S3_TRIES 重试，仍失败则抛出（fail-fast）。

    重试次数在调用时读取（而不是模块导入时），避免模块加载阶段产生副作用；
    取值合法性由启动校验保证（非法值直接报错，不再静默兜底）。
    """
    tries = int(os.environ.get("RESULT_TO_S3_TRIES", 3))

    @retry(tries=tries, delay=1, backoff=2)
    def attempt():
        client.put_object(
            bucket,
            key,
            io.BytesIO(data),
            length=len(data),
            content_type=_CONTENT_TYPE_JSON,
        )

    attempt()


def finalize_task_result(raw, task_id, result_model=None, offload=True):
    """任务返回时的收口：结构校验 → 规范化 → 按配置决定存储位置。

    ``result_model`` 为空（任务未定义 Result）时跳过校验，仅做规范化尝试。
    ``offload=False``（同步执行场景：结果即时消费）时只做校验与规范化，
    不写入对象存储 —— /run 的语义是“直接拿到结果”。
    """
    if result_model is not None:
        payload = result_model.model_validate(raw).model_dump(mode="json")
    elif hasattr(raw, "model_dump"):
        payload = raw.model_dump(mode="json")
    else:
        payload = raw

    if not offload or not is_s3_enabled():
        return payload

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if not should_offload(len(data)):
        return payload

    client = get_s3_client()
    key = build_object_key(task_id)
    put_result_object(client, get_bucket(), key, data)
    return make_storage_payload(task_id, data)


# --------------------------------------------------------------------------- #
# 预签名（master 侧，check 时调用）
# --------------------------------------------------------------------------- #
def _iso_utc(dt):
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_s3_result_response(payload):
    """把存储层 payload 转成返回给客户端的结果引用。

    ``url`` 是**相对路径**（形如 ``/<bucket>/<key>?X-Amz-...``）：客户端拼上自己
    访问 FastTask 的地址即可下载，所以服务端不必知道对外地址，也不需要额外暴露
    对象存储端口（代理会在转发时把 Host 统一改回内部地址）。

    预签名失败不阻塞状态查询：``url`` 置空并记录 ``url_error``，
    避免调用方把“引用存在但不可下载”误当成内联结果。
    """
    response = {
        "uri": payload.get("uri"),
        "size_bytes": payload.get("size_bytes"),
        "sha256": payload.get("sha256"),
        "url": None,
        "expires_at": None,
        "url_error": None,
    }

    expires = get_presign_expires()
    try:
        signed_url = get_s3_client().presigned_get_object(
            get_bucket(), payload["key"], expires=timedelta(seconds=expires)
        )
        parsed = urlparse(signed_url)
        response["url"] = f"{parsed.path}?{parsed.query}"
        response["expires_at"] = _iso_utc(
            datetime.now(timezone.utc) + timedelta(seconds=expires)
        )
    except Exception as error:  # noqa: BLE001 - 不阻塞状态查询，但必须明确暴露
        response["url_error"] = repr(error)
        print(f"FastTask ---> presign failed for {payload.get('key')!r}: {error!r}")

    return response


# --------------------------------------------------------------------------- #
# bucket 自检与清理
# --------------------------------------------------------------------------- #
def ensure_bucket():
    """启动自检：确保 bucket 存在。配置错误时立刻失败，而不是等任务跑完才炸。"""
    client = get_s3_client()
    bucket = get_bucket()
    if client.bucket_exists(bucket):
        return
    try:
        client.make_bucket(bucket)
        print(f"FastTask ---> created bucket: {bucket}")
    except Exception:
        # 多个 uvicorn worker 并发自检时，可能已被其它 worker 创建成功
        if not client.bucket_exists(bucket):
            raise


def cleanup_expired_objects(expiration_seconds):
    """删除超过保留期的结果对象，返回删除数量。"""
    client = get_s3_client()
    bucket = get_bucket()
    cutoff = datetime.now(timezone.utc) - timedelta(seconds=expiration_seconds)

    removed = 0
    for obj in client.list_objects(
        bucket, prefix=get_object_prefix(), recursive=True
    ):
        last_modified = obj.last_modified
        if last_modified is not None and last_modified < cutoff:
            client.remove_object(bucket, obj.object_name)
            removed += 1
            print(f"FastTask ---> removed expired object: {obj.object_name}")

    return removed
