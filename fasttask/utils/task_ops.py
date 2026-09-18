"""任务操作：创建 / 查询 / 同步执行。

HTTP 接口（``api.py``）与 MCP 工具（``mcp_server.py``）共用这里的实现，
避免同一套业务逻辑写两遍导致两条通道语义漂移
（历史上 ``/run`` 与 MCP ``run_*`` 就各自处理结果外置，行为并不一致）。

这里返回的都是普通 dict，字段语义固定：

    {"result_id": ..., "state": ..., "result_type": ..., "result": ...}

接口层只负责把它包装成自己的协议（HTTP 的 ``ResultInfo`` / MCP 的 JSON 文本）。
"""

import traceback
import uuid
from importlib import import_module

from celery_app import app as celery_app

from utils.api_utils import TaskState
from utils.result_storage import (
    RESULT_TYPE_JSON,
    RESULT_TYPE_S3,
    RESULT_TYPE_TEXT,
    build_s3_result_response,
    detect_stored_result_type,
)


def load_task(task_name):
    """取运行期生成的 Celery 任务对象（``loaded_tasks._<task_name>``）。"""
    module = import_module(package="loaded_tasks", name=f".{task_name}")
    return getattr(module, f"_{task_name}")


def load_task_model(task_name, name):
    """取任务模块里的 ``Params`` / ``Result`` 模型；属性不存在时返回 None。

    模块本身导入失败不吞异常：与 ``api.py`` 注册路由时的行为保持一致。
    """
    module = import_module(package="tasks", name=f".{task_name}")
    return getattr(module, name, None)


def task_doc(task_name):
    """任务模块的 docstring（作为接口/工具说明）。取不到时返回 None。"""
    try:
        module = import_module(package="tasks", name=f".{task_name}")
    except Exception:  # noqa: BLE001 - docstring 是可选的说明信息
        return None
    return (module.__doc__ or "").strip() or None


def describe_success(raw_result, result_model=None):
    """把成功结果翻译成 ``(result_type, payload)``。

    结果已外置到对象存储时只返回引用（含预签名下载地址），不回传内容。
    """
    if detect_stored_result_type(raw_result) == RESULT_TYPE_S3:
        return RESULT_TYPE_S3, build_s3_result_response(raw_result)

    value = raw_result
    if result_model is not None:
        value = result_model.model_validate(raw_result)
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return RESULT_TYPE_JSON, value


def _payload(result_id, state, result_type=RESULT_TYPE_JSON, result="", **extra):
    """统一的结果结构。所有返回都经这里构造，避免漏字段。"""
    return {
        "result_id": result_id,
        "state": state,
        "result_type": result_type,
        "result": result,
        **extra,
    }


def _task_kwargs(params_dict):
    """任务包装层要求带上并发参数键（未指定时为 None）。"""
    return {**params_dict, "fasttask_concurrency_params": None}


def create_task(task_name, params_dict, running_id):
    """创建异步任务，返回 ``{result_id, state, result_type, result, hint}``。

    与 check / run 保持同一结构（此时还没结果，result 为空）。
    """
    async_result = load_task(task_name).apply_async(
        args=(),
        kwargs=_task_kwargs(params_dict),
        task_id=f"{running_id}-{uuid.uuid4()}",
        queue=task_name,
    )
    return _payload(
        async_result.id,
        async_result.state,
        hint=f"用 check_{task_name}(result_id=...) 查询状态与结果",
    )


def check_task(result_id, running_id, result_model=None):
    """查询任务状态与结果。"""
    if not result_id.startswith(running_id):
        return _payload(
            result_id,
            TaskState.failure.value,
            RESULT_TYPE_TEXT,
            f"{result_id} 不存在；当前服务实例标识为 {running_id}",
        )

    async_result = celery_app.AsyncResult(result_id)
    state = async_result.state
    raw_result = async_result.result

    if state == TaskState.success.value:
        result_type, payload = describe_success(raw_result, result_model)
    elif state == TaskState.failure.value:
        result_type = RESULT_TYPE_TEXT
        payload = f"{raw_result!r}\n{async_result.traceback}"
    else:
        result_type = RESULT_TYPE_TEXT
        payload = str(raw_result)

    return _payload(result_id, state, result_type, payload)


def run_task_sync(task_name, params_dict, task_id, result_model=None):
    """同步执行任务并返回结果（本地立即执行，不进入队列）。

    用 Celery 的 ``apply()`` 而不是直接调用任务对象：直接调用时
    ``self.request.id`` 为 None，一旦结果需要外置就会产生互相覆盖的
    ``None.json``。任务包装层对 is_eager 的执行不做外置，因此这里拿到的是
    真实结果（run 的语义就是直接拿结果）。
    """
    try:
        eager = load_task(task_name).apply(
            args=(),
            kwargs=_task_kwargs(params_dict),
            task_id=task_id,
        )
    except Exception:  # noqa: BLE001 - 任务模块加载失败等
        return _payload(
            task_id, TaskState.failure.value, RESULT_TYPE_TEXT, traceback.format_exc()
        )

    if eager.state != TaskState.success.value:
        return _payload(
            task_id,
            eager.state,
            RESULT_TYPE_TEXT,
            f"{eager.result!r}\n{eager.traceback}",
        )

    result_type, payload = describe_success(eager.result, result_model)
    return _payload(task_id, TaskState.success.value, result_type, payload)


def new_task_id(running_id):
    return f"{running_id}-{uuid.uuid4()}"
