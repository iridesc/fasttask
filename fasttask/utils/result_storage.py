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

from retry import retry

# 存储层标记：结果 payload 顶层出现该字段即表示"内容已搬到对象存储"
STORAGE_MARKER = "__fasttask_storage__"

RESULT_TYPE_JSON = "json"
RESULT_TYPE_S3 = "s3"
RESULT_TYPE_TEXT = "text"

_RESULT_TYPE_S3_UPPER = "S3"
_RESULT_TYPE_AUTO_UPPER = "AUTO"

_CONTENT_TYPE_JSON = "application/json"
_DEFAULT_AUTO_OFFLOAD_SIZE = 1024 * 1024
_DEFAULT_PRESIGN_EXPIRES = 24 * 60 * 60
_DEFAULT_S3_PORT = "9000"
_DEFAULT_S3_BUCKET = "fasttask-results"

_s3_client = None
_s3_client_endpoint = None
_public_s3_client = None
_public_s3_client_endpoint = None


# --------------------------------------------------------------------------- #
# 配置读取
# --------------------------------------------------------------------------- #
def get_configured_result_type():
    """返回 RESULT_TYPE 的大写取值，默认为 JSON（保持历史行为）。"""
    return os.environ.get("RESULT_TYPE", "JSON").strip().upper()


def is_s3_enabled():
    """是否需要对象存储参与（S3 与 AUTO 模式都需要）。"""
    return get_configured_result_type() in (_RESULT_TYPE_S3_UPPER, _RESULT_TYPE_AUTO_UPPER)


def get_auto_offload_size():
    return int(os.environ.get("RESULT_AUTO_TO_S3_SIZE", _DEFAULT_AUTO_OFFLOAD_SIZE))


def get_presign_expires():
    return int(os.environ.get("S3_PRESIGN_EXPIRES", _DEFAULT_PRESIGN_EXPIRES))


def get_bucket():
    return os.environ.get("S3_BUCKET") or _DEFAULT_S3_BUCKET


def get_s3_port():
    return os.environ.get("S3_PORT", _DEFAULT_S3_PORT)


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


def get_s3_public_endpoint():
    """预签名下载地址对外暴露的地址（客户端访问用）。

    容器部署时服务端连的是容器内的 `127.0.0.1:9000`，而下载方在容器外，
    因此预签名 URL 需要以对外可达的地址（域名或宿主 IP:端口）来生成。
    未配置时沿用 `S3_ENDPOINT`（适用于客户端与服务端同网络的场景）。
    """
    return (os.environ.get("S3_PUBLIC_ENDPOINT") or "").strip()


def get_public_s3_client():
    """生成预签名 URL 专用客户端（endpoint 为对外地址）。

    SigV4 签名包含 Host 头，所以不能“事后把 URL 的主机名换掉”——
    必须直接以对外地址计算签名，否则对象存储侧校验会失败（403 SignatureDoesNotMatch）。
    `presigned_get_object` 只做签名计算、不发起请求，因此对外地址即使当前不可达也没关系。
    """
    global _public_s3_client, _public_s3_client_endpoint

    public_endpoint = get_s3_public_endpoint()
    if not public_endpoint:
        return get_s3_client()

    if _public_s3_client is None or _public_s3_client_endpoint != public_endpoint:
        from minio import Minio

        access_key, secret_key = derive_s3_credentials()
        public_secure = os.environ.get("S3_PUBLIC_SECURE", "").strip()
        secure = (
            public_secure == "True"
            if public_secure
            else os.environ.get("S3_SECURE", "False") == "True"
        )
        _public_s3_client = Minio(
            public_endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=secure,
            region=os.environ.get("S3_REGION") or None,
            cert_check=os.environ.get("S3_VERIFY_SSL", "True") == "True",
        )
        _public_s3_client_endpoint = public_endpoint
    return _public_s3_client


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


def get_s3_client():
    """延迟构建 S3 客户端：RESULT_TYPE=JSON 时不引入 minio 依赖。"""
    global _s3_client, _s3_client_endpoint

    endpoint = get_s3_endpoint()
    if _s3_client is None or _s3_client_endpoint != endpoint:
        from minio import Minio

        access_key, secret_key = derive_s3_credentials()
        _s3_client = Minio(
            endpoint,
            access_key=access_key,
            secret_key=secret_key,
            secure=os.environ.get("S3_SECURE", "False") == "True",
            region=os.environ.get("S3_REGION") or None,
            cert_check=os.environ.get("S3_VERIFY_SSL", "True") == "True",
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
    mode = get_configured_result_type()
    if mode == _RESULT_TYPE_S3_UPPER:
        return True
    if mode == _RESULT_TYPE_AUTO_UPPER:
        return size_bytes > get_auto_offload_size()
    return False


# --------------------------------------------------------------------------- #
# 存储层 payload
# --------------------------------------------------------------------------- #
def make_storage_payload(result_id, data):
    """构造写入 Celery backend 的引用对象（内容已上传对象存储）。"""
    key = build_object_key(result_id)
    return {
        STORAGE_MARKER: RESULT_TYPE_S3,
        "key": key,
        "uri": f"s3://{get_bucket()}/{key}",
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


def detect_stored_result_type(raw):
    """判断存储层里的结果类型（历史数据没有标记，视为 json）。"""
    if isinstance(raw, dict) and raw.get(STORAGE_MARKER) == RESULT_TYPE_S3:
        return RESULT_TYPE_S3
    return RESULT_TYPE_JSON


# --------------------------------------------------------------------------- #
# 上传（worker 侧）
# --------------------------------------------------------------------------- #
@retry(
    tries=max(int(os.environ.get("RESULT_TO_S3_TRIES", 3)), 1),
    delay=1,
    backoff=2,
)
def put_result_object(client, bucket, key, data):
    """上传结果对象；失败按 RESULT_TO_S3_TRIES 重试，仍失败则抛出（fail-fast）。"""
    client.put_object(
        bucket,
        key,
        io.BytesIO(data),
        length=len(data),
        content_type=_CONTENT_TYPE_JSON,
    )


def finalize_task_result(raw, task_id, result_model=None):
    """任务返回时的收口：结构校验 → 规范化 → 按配置决定存储位置。

    ``result_model`` 为空（任务未定义 Result）时跳过校验，仅做规范化尝试。
    """
    if result_model is not None:
        payload = result_model.model_validate(raw).model_dump(mode="json")
    elif hasattr(raw, "model_dump"):
        payload = raw.model_dump(mode="json")
    else:
        payload = raw

    if not is_s3_enabled():
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


def build_s3_result_response(payload, presign=True):
    """把存储层 payload 转成返回给客户端的结果引用（含预签名下载地址）。

    预签名失败不阻塞状态查询：``url`` 置空并记录日志，
    客户端仍可凭 ``uri`` 自行取数。
    """
    response = {
        "uri": payload.get("uri"),
        "size_bytes": payload.get("size_bytes"),
        "sha256": payload.get("sha256"),
        "url": None,
        "expires_at": None,
    }
    if not presign:
        return response

    expires = get_presign_expires()
    try:
        # 用对外地址签名（而非事后替换主机名），保证签名与下载方发出的 Host 一致
        client = get_public_s3_client()
        response["url"] = client.presigned_get_object(
            get_bucket(), payload["key"], expires=timedelta(seconds=expires)
        )
        response["expires_at"] = _iso_utc(
            datetime.now(timezone.utc) + timedelta(seconds=expires)
        )
    except Exception as error:  # noqa: BLE001 - 预签名失败降级为不带 url
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
