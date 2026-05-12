from enum import Enum
import os
import json
import datetime
import uuid
import base64
import secrets
import string
from lazy_action.lazy_action import lazy_action
from fastapi import HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi import Depends, HTTPException, status, UploadFile
from typing import Any, Annotated
from redis.asyncio import Redis
import asyncio
import httpx
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


CONF_DIR = os.environ["CONF_DIR"]
redis_params = {
    "host": os.environ["MASTER_HOST"],
    "port": os.environ["TASK_QUEUE_PORT"],
    "password": os.environ["TASK_QUEUE_PASSWD"],
    "decode_responses": True,
    "socket_connect_timeout": 5,
}


class TaskState(Enum):
    pending = "PENDING"
    started = "STARTED"
    failure = "FAILURE"
    success = "SUCCESS"
    revoked = "REVOKED"
    retry = "RETRY"


def check_file_name(file_name: str, username: str) -> str:
    ALLOWED_BASE_DIR = os.path.abspath("./files/")
    DISALLOWED_SUB_DIR = os.path.abspath("./files/fasttask/")

    if ".." in file_name or file_name.startswith("/") or file_name.startswith("\\"):
        print(f"SECURITY ALERT: {username=} 尝试目录遍历: {file_name=}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="无效文件名: 不允许目录遍历尝试。",
        )

    full_path_proposed = os.path.abspath(os.path.join(ALLOWED_BASE_DIR, file_name))

    if not full_path_proposed.startswith(ALLOWED_BASE_DIR):
        print(f"SECURITY ALERT: {username=} 尝试访问基础目录之外的文件: {file_name=}")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="无效文件路径: 必须在指定的文件目录内。",
        )

    if full_path_proposed.startswith(DISALLOWED_SUB_DIR):
        print(f"SECURITY ALERT: {username=} 尝试访问禁止目录: {file_name=}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="禁止访问此特定目录。",
        )
    return full_path_proposed


def get_safe_file_name(file_name: str, username: str) -> str:
    full_path_proposed = check_file_name(file_name, username)
    return f"{uuid.uuid4()}_{os.path.basename(full_path_proposed)}"


@lazy_action(mode="memory")
def load_user_to_passwd() -> dict:
    auth_file = f"{CONF_DIR}/user_to_passwd.json"
    if not os.path.exists(auth_file):
        return dict()
    with open(auth_file, "r") as f:
        return json.load(f)


def get_task_statistics_info(task_infos, end_time, task_name=None):
    time_range_key_to_seconds = {
        "1_minute": 60,
        "15_minute": 900,
        "1_hour": 3600,
        "1_day": 86400,
    }
    completed_counts = dict()
    status_to_amount = dict()
    for task_info in task_infos:
        if task_name and task_info["task"] != task_name:
            continue
        status = task_info["status"]
        status_to_amount.setdefault(status, 0)
        status_to_amount[status] += 1

        for status in ["SUCCESS", "FAILURE"]:
            if task_info["status"] != status:
                continue

            completed_at = datetime.datetime.fromisoformat(task_info["date_done"])

            for time_range_key, seconds in time_range_key_to_seconds.items():
                counter_name = f"{status.lower()}_in_{time_range_key}"
                completed_counts.setdefault(counter_name, 0)
                if (
                    end_time - datetime.timedelta(seconds=seconds)
                    <= completed_at
                    <= end_time
                ):
                    completed_counts[counter_name] += 1

    throughput = dict()
    for status_prefix in ["success", "failure"]:
        for window_suffix, seconds in time_range_key_to_seconds.items():
            count_key = f"{status_prefix}_in_{window_suffix}"

            throughput[count_key] = (
                round(completed_counts.get(count_key, 0) / seconds, 2)
                if seconds > 0
                else 0.0
            )

    return {
        "status_to_amount": status_to_amount,
        "completed_counts": completed_counts,
        "throughput_rates_per_second": throughput,
    }


def get_current_username(
    credentials: Annotated[HTTPBasicCredentials, Depends(HTTPBasic())],
):
    user_to_passwd = load_user_to_passwd()
    if not user_to_passwd:
        return "Anonymous"

    if not (
        credentials.username in user_to_passwd
        and secrets.compare_digest(
            credentials.password.encode("utf8"),
            user_to_passwd[credentials.username].encode("utf8"),
        )
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="UNAUTHORIZED",
            headers={"WWW-Authenticate": "Basic"},
        )

    return credentials.username


def generate_id(length=12):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


async def initialize_running_id():
    db_key = "fasttask:current_running_id"

    async with await Redis(
        db=0,
        **redis_params,
    ) as r:
        persisted_running_id = await r.get(db_key)
        if persisted_running_id:
            print(f"Using persisted RUNNING_ID: {persisted_running_id}")
            return persisted_running_id

        running_id = generate_id(8)
        running_id = (
            running_id
            if await r.set(db_key, running_id, nx=True)
            else await r.get(db_key)
        )
        print(f"Generated new RUNNING_ID to redis: {running_id}")
        return running_id


def try_import_Data(task_model, DataName) -> type:
    try:
        return getattr(task_model, DataName)
    except Exception as error:
        print(f"{task_model=} {DataName=} not found! {error=}")
        return Any


async def load_redis_task_infos(task_names) -> dict:
    task_id_to_infos = dict()

    async with Redis(
        **redis_params,
        db=1,
    ) as r:
        for task_name in task_names:
            for i in range(await r.llen(task_name)):
                raw_data = await r.lindex(task_name, i)
                if not raw_data:
                    continue
                raw_info = json.loads(raw_data)
                task_id_to_infos[raw_info["headers"]["id"]] = {
                    "task": task_name,
                    "status": TaskState.pending.value,
                    "date_done": None,
                }

    async with Redis(
        **redis_params,
        db=2,
    ) as r:
        async for key in r.scan_iter(match="*"):
            raw_info = await r.get(key)
            if not raw_info:
                continue
            raw_info = json.loads(raw_info)
            task_id_to_infos[raw_info["task_id"]] = {
                "task": raw_info["name"].split(".")[1],
                "status": raw_info["status"],
                "date_done": raw_info["date_done"],
            }
    return task_id_to_infos


@lazy_action(expire=10, mode="memory")
def _get_worker_status(celery_app) -> dict:
    def get_task_infos(worker_name, handle):
        return [info["id"] for info in handle.get(worker_name, [])]

    def get_worker_info(worker_name):
        info = inspector.stats().get(worker_name, {})
        return {
            "total": info.get("total"),
            "uptime": info.get("uptime"),
            "pool.max-concurrency": info.get("pool", dict()).get("max-concurrency"),
            "prefetch_count": info.get("prefetch_count"),
            "rusage.maxrss": info.get("rusage", dict()).get("maxrss"),
        }

    inspector = celery_app.control.inspect()
    ping_result = inspector.ping()
    online_worker_names = [] if ping_result is None else list(ping_result.keys())
    return {
        "online_worker_count": len(online_worker_names),
        "online_workers_list": online_worker_names,
        "worker_details": {
            worker_name: {
                "active_tasks": get_task_infos(worker_name, inspector.active()),
                "reserved_tasks": get_task_infos(worker_name, inspector.reserved()),
                "scheduled_tasks": get_task_infos(worker_name, inspector.scheduled()),
                **get_worker_info(worker_name),
            }
            for worker_name in online_worker_names
        },
    }


async def get_worker_status(celery_app) -> dict:
    return await asyncio.to_thread(_get_worker_status, celery_app)


def upload_sync(file: UploadFile, username):
    file_name = get_safe_file_name(file.filename, username)
    with open(
        os.path.join(
            "./files/",
            file_name,
        ),
        "wb",
    ) as f:
        for i in iter(lambda: file.file.read(1024 * 1024 * 10), b""):
            f.write(i)
    return file_name


async def get_pending_task_count(task_names):
    async with await Redis(
        db=1,
        **redis_params,
    ) as r:
        pipe = r.pipeline()
        for task_name in task_names:
            pipe.llen(task_name)
    return dict(zip(task_names, await pipe.execute()))


def truncate_body(body: bytes, max_len: int = 200) -> str:
    try:
        text = body.decode("utf-8", errors="replace")
    except:
        text = body.decode("latin-1", errors="replace")

    if len(text) > max_len:
        return f"{text[:max_len]}... [truncated]"
    return text


class FlowerProxyMiddleware(BaseHTTPMiddleware):
    """处理 /flower 路径的认证和代理"""

    async def dispatch(self, request: Request, call_next):
        # 只处理 /flower 路径
        if not request.url.path.startswith("/flower"):
            return await call_next(request)

        # 检查认证
        user_to_passwd = load_user_to_passwd()

        # 如果配置了认证文件，需要验证
        if user_to_passwd:
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Basic "):
                return Response(
                    content=json.dumps({"detail": "Not authenticated"}),
                    status_code=401,
                    headers={"WWW-Authenticate": "Basic"},
                    media_type="application/json",
                )

            try:
                decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
                username, password = decoded.split(":", 1)
            except Exception:
                return Response(
                    content=json.dumps({"detail": "Invalid authentication"}),
                    status_code=401,
                    headers={"WWW-Authenticate": "Basic"},
                    media_type="application/json",
                )

            # 验证用户名密码
            if not (
                username in user_to_passwd
                and secrets.compare_digest(
                    password.encode("utf8"),
                    user_to_passwd[username].encode("utf8"),
                )
            ):
                return Response(
                    content=json.dumps({"detail": "Invalid credentials"}),
                    status_code=401,
                    headers={"WWW-Authenticate": "Basic"},
                    media_type="application/json",
                )

        # 认证通过，代理请求到 Flower
        path = request.url.path
        # Flower 需要尾部斜杠，如果访问 /flower 则添加
        if path == "/flower":
            path = "/flower/"

        # 构造完整 URL，包含查询参数
        query = request.url.query
        flower_url = f"http://localhost:{os.environ['FLOWER_PORT']}{path}"
        if query:
            flower_url = f"{flower_url}?{query}"

        # 构造代理请求
        headers = dict(request.headers)
        headers.pop("host", None)

        # 获取请求体
        body = await request.body()

        async with httpx.AsyncClient(timeout=60.0) as client:
            try:
                # 发送代理请求
                proxy_response = await client.request(
                    method=request.method,
                    url=flower_url,
                    headers=headers,
                    content=body,
                    follow_redirects=False,
                )

                # 构造响应
                response_headers = dict(proxy_response.headers)
                response_headers.pop("content-encoding", None)
                response_headers.pop("content-length", None)
                response_headers.pop("transfer-encoding", None)

                return Response(
                    content=proxy_response.content,
                    status_code=proxy_response.status_code,
                    headers=response_headers,
                    media_type=proxy_response.headers.get("content-type"),
                )
            except httpx.RequestError as e:
                return Response(
                    content=json.dumps({"detail": f"Flower service error: {str(e)}"}),
                    status_code=503,
                    media_type="application/json",
                )


class LoggingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        print(f"\n{'=' * 50}")
        print(f"📥 Request: {request.method} {request.url}")
        print(f"   Headers: {dict(request.headers)}")

        body = await request.body()
        if body:
            content_type = request.headers.get("content-type", "")
            if "application/json" in content_type:
                try:
                    print(f"   Body: {json.loads(body)}")
                except:
                    print(f"   Body: {truncate_body(body)}")
            else:
                print(f"   Body: {truncate_body(body)}")

        async def receive():
            return {"type": "http.request", "body": body}

        request._receive = receive

        response = await call_next(request)

        response_body = b""
        async for chunk in response.body_iterator:
            response_body += chunk

        print(f"📤 Response: {response.status_code}")
        print(f"   Headers: {dict(response.headers)}")

        content_type = response.headers.get("content-type", "")
        if "application/json" in content_type:
            try:
                print(f"   Body: {json.loads(response_body)}")
            except:
                print(f"   Body: {truncate_body(response_body)}")
        else:
            print(f"   Body: {truncate_body(response_body)}")

        print(f"{'=' * 50}\n")

        return Response(
            content=response_body,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )
