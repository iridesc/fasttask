import datetime
import json
import os
import sys
import uuid
import secrets
import traceback
import redis
import secrets
import string
from enum import Enum
from typing import Any, Union, Annotated
from importlib import import_module
from pydantic import BaseModel
from starlette.responses import FileResponse
from fastapi import Depends, FastAPI, HTTPException, status, UploadFile
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from celery_app import app as celery_app
from tools import (
    check_file_name,
    get_bool_env,
    get_safe_file_name,
    load_task_names,
)
from setting import project_title, project_description, project_summary, project_version

sys.path.append("tasks")

TASK_NAMES = load_task_names("tasks")

CONF_DIR = os.environ["CONF_DIR"]
redis_params = {
    "host": os.environ["MASTER_HOST"],
    "port": os.environ["TASK_QUEUE_PORT"],
    "password": os.environ["TASK_QUEUE_PASSWD"],
    "decode_responses": True,
    "socket_connect_timeout": 5,
}


def generate_id(length=12):
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def initialize_running_id():
    global RUNNING_ID
    db_key = "fasttask:current_running_id"
    with redis.StrictRedis(
        **redis_params,
        db=0,
    ) as r:
        persisted_running_id = r.get(db_key)
        if persisted_running_id:
            RUNNING_ID = persisted_running_id
            print(f"Using persisted RUNNING_ID: {RUNNING_ID}")
            return

        RUNNING_ID = generate_id(8)
        RUNNING_ID = RUNNING_ID if r.set(db_key, RUNNING_ID, nx=True) else r.get(db_key)
        print(f"Generated new RUNNING_ID to redis: {RUNNING_ID}")


initialize_running_id()


class TaskState(Enum):
    pending = "PENDING"
    started = "STARTED"
    failure = "FAILURE"
    success = "SUCCESS"
    revoked = "REVOKED"
    retry = "RETRY"


class ActionStatus(Enum):
    success = "SUCCESS"
    failure = "FAILURE"


class ActionResp(BaseModel):
    status: ActionStatus = ActionStatus.failure
    result: Any = ""
    message: str = ""


class DownloadFileInfo(BaseModel):
    file_name: str = "lp.jpg"


class ResultIDParams(BaseModel):
    result_id: str


def load_user_to_passwd() -> dict:
    auth_file = f"{CONF_DIR}/user_to_passwd.json"
    if not os.path.exists(auth_file):
        return dict()
    with open(auth_file, "r") as f:
        return json.load(f)


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


def try_import_Data(task_model, DataName) -> type:
    try:
        return getattr(task_model, DataName)
    except Exception as error:
        print(f"{task_model=} {DataName=} not found! {error=}")
        return Any


def load_redis_task_infos() -> dict:
    task_id_to_infos = dict()

    with redis.StrictRedis(
        **redis_params,
        db=1,
    ) as r:
        for task_name in TASK_NAMES:
            for i in range(r.llen(task_name)):
                raw_data = r.lindex(task_name, i)
                if not raw_data:
                    continue
                raw_info = json.loads(raw_data)
                task_id_to_infos[raw_info["headers"]["id"]] = {
                    "task": task_name,
                    "status": TaskState.pending.value,
                    "date_done": None,
                }

    with redis.StrictRedis(
        **redis_params,
        db=2,
    ) as r:
        for key in r.scan_iter(match="*"):
            raw_info = json.loads(r.get(key))
            task_id_to_infos[raw_info["task_id"]] = {
                "task": raw_info["name"].split(".")[1],
                "status": raw_info["status"],
                "date_done": raw_info["date_done"],
            }
    return task_id_to_infos


def get_worker_status() -> dict:
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
    online_worker_names = list(inspector.ping().keys())
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


doc_url = None
if get_bool_env("API_DOCS"):
    doc_url = "/docs"

redoc_url = None
if get_bool_env("API_REDOC"):
    redoc_url = "/redoc"


app = FastAPI(
    title=project_title,
    description=project_description,
    summary=project_summary,
    version=project_version,
    docs_url=doc_url,
    redoc_url=redoc_url,
)


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


if get_bool_env("API_STATUS_INFO"):

    @app.get("/status_info")
    def status_info(username: Annotated[str, Depends(get_current_username)]):
        info = {
            "running_id": RUNNING_ID,
            "username": username,
            "worker_status": get_worker_status(),
        }

        task_infos = load_redis_task_infos().values()
        end_time = datetime.datetime.now(datetime.timezone.utc)

        info["task_info_total"] = get_task_statistics_info(
            end_time=end_time, task_infos=task_infos
        )

        for task_name in TASK_NAMES:
            info[f"task_info_{task_name}"] = get_task_statistics_info(
                end_time=end_time, task_infos=task_infos, task_name=task_name
            )

        return info


if get_bool_env("API_FILE_DOWNLOAD"):

    @app.get("/download")
    def download(file_name, username: Annotated[str, Depends(get_current_username)]):
        validated_file_path = check_file_name(file_name, username)
        if not os.path.isfile(validated_file_path):
            return HTTPException(status_code=404, detail="File not found")
        display_filename = os.path.basename(validated_file_path)
        print(f"{username=}: 下载: {display_filename=} {validated_file_path=}")
        return FileResponse(validated_file_path, filename=display_filename)


if get_bool_env("API_FILE_UPLOAD"):

    @app.post("/upload")
    def upload(
        file: UploadFile, username: Annotated[str, Depends(get_current_username)]
    ):
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

        return {"file_name": file_name}


if get_bool_env("API_REVOKE"):

    @app.post(f"/revoke", response_model=ActionResp)
    def revoke(
        result_id_params: ResultIDParams,
        username: Annotated[str, Depends(get_current_username)],
    ):
        resp = ActionResp()
        result_id = result_id_params.result_id
        if not result_id.startswith(RUNNING_ID):
            resp.message = f"invalid {result_id=} current {RUNNING_ID=}"
            return resp

        async_result = celery_app.AsyncResult(result_id)

        state = async_result.state
        async_result.revoke(terminate=True)

        if state in [
            TaskState.success.value,
            TaskState.failure.value,
            TaskState.revoked.value,
        ]:
            resp.message = "task ended or revoked already"
            resp.status = ActionStatus.success

        elif state == TaskState.pending.value:
            resp.message = "task is still pending, will revoked later"
            resp.status = ActionStatus.success

        elif state == TaskState.started.value:
            resp.message = "task started, revoking now"
            resp.status = ActionStatus.success

        elif state == TaskState.retry.value:
            resp.message = "task retrying, revoking now"
            resp.status = ActionStatus.success

        else:
            resp.message = f"unknown task state {state=}"
            resp.status = ActionStatus.failure

        return resp


def get_task_apis(task_name):
    task = getattr(
        import_module(package="loaded_tasks", name=f".{task_name}"), f"_{task_name}"
    )

    task_model = import_module(package="tasks", name=f".{task_name}")
    Result = try_import_Data(task_model, "Result")
    Params = try_import_Data(task_model, "Params")

    class ResultInfo(BaseModel):
        id: str = ""
        state: TaskState = TaskState.failure.value
        result: Union[Result, str]

    if get_bool_env("API_RUN"):

        @app.post(f"/run/{task_name}", response_model=ResultInfo)
        def run(
            params: Params, username: Annotated[str, Depends(get_current_username)]
        ):
            try:
                result = Result(**task(**params.model_dump()))
                state = TaskState.success.value
            except Exception:
                result = traceback.format_exc()
                state = TaskState.failure.value

            return ResultInfo(result=result, state=state)

    if get_bool_env("API_CREATE"):

        @app.post(f"/create/{task_name}", response_model=ResultInfo)
        def create(
            params: Params, username: Annotated[str, Depends(get_current_username)]
        ):
            try:
                async_result = task.apply_async(
                    args=(),
                    kwargs=params.model_dump(),
                    task_id=f"{RUNNING_ID}-{uuid.uuid4()}",
                    queue=task_name,
                )
            except Exception:
                result_info = ResultInfo(result=traceback.format_exc())
            else:
                result_info = ResultInfo(
                    id=async_result.id, state=async_result.state, result=""
                )

            return result_info

    if get_bool_env("API_CHECK"):

        @app.get(f"/check/{task_name}", response_model=ResultInfo)
        def check(
            result_id: str, username: Annotated[str, Depends(get_current_username)]
        ):
            if not result_id.startswith(RUNNING_ID):
                return ResultInfo(
                    id=result_id,
                    state=TaskState.failure.value,
                    result=f"{result_id=} not exist, current {RUNNING_ID=}",
                )

            async_result = celery_app.AsyncResult(result_id)
            if async_result.state == TaskState.success.value:
                result = Result(**async_result.result)
            elif async_result.state == TaskState.failure.value:
                result = str(async_result.traceback)
            else:
                result = str(async_result.result)

            return ResultInfo(id=result_id, state=async_result.state, result=result)


for task_name in TASK_NAMES:
    get_task_apis(task_name)
