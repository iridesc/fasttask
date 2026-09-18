import os
import shutil

task_file_template = """import sys
from celery_app import app

sys.path.append("tasks")
from utils.redis_lock import RedisConcurrencyController
from utils.result_storage import finalize_task_result
from tasks import {task_name} as _task_module

_task_func = getattr(_task_module, "{task_name}")
_task_result_model = getattr(_task_module, "Result", None)


@app.task(bind=True, soft_time_limit={soft_time_limit}, time_limit={time_limit})
def _{task_name}(self, *args, **kwargs):

    fasttask_concurrency_params = kwargs.pop('fasttask_concurrency_params', None)

    if fasttask_concurrency_params is not None:
        concurrency_key = "fasttask:lock:"+"{task_name}:"+str(fasttask_concurrency_params['concurrency_key'])
        max_concurrency = fasttask_concurrency_params['max_concurrency']
        expire = fasttask_concurrency_params['expire']
        controller = RedisConcurrencyController(max_concurrent=max_concurrency, expire=expire)
        if controller.acquire(concurrency_key):
            try:
                raw_result = _task_func(*args, **kwargs)
            finally:
                controller.release(concurrency_key)
        else:
            countdown = fasttask_concurrency_params['countdown']
            raise self.retry(countdown=countdown)
    else:
        raw_result = _task_func(*args, **kwargs)

    # 统一收口：Result 结构校验（fail-fast）+ 规范化 + 按 RESULT_TYPE 决定去向。
    # 同步执行（/run 与 MCP 的 run_* 走 apply，request.is_eager=True）时结果即时消费，
    # 不做外置，以保持“run 直接返回结果”的语义。
    return finalize_task_result(
        raw_result,
        task_id=self.request.id,
        result_model=_task_result_model,
        offload=not self.request.is_eager and self.request.id is not None,
    )
"""


def rm_tmp_folder(folder_path):
    if os.path.exists(folder_path):
        shutil.rmtree(folder_path)


def get_bool_env(name):
    return os.environ.get(name, "False") == "True"


def get_list_env(name):
    return [s.strip() for s in os.environ.get(name, "").split(",") if s.strip()]


def load_task_names(
    folder_path, enabled_tasks: list = None, disabled_tasks: list = None
):
    task_names = set()
    for task_name in [
        py_file[:-3] for py_file in os.listdir(folder_path) if py_file.endswith(".py")
    ]:
        if enabled_tasks and task_name not in enabled_tasks:
            continue
        if disabled_tasks and task_name in disabled_tasks:
            continue
        task_names.add(task_name)
    return task_names


def load_tasks(
    from_folder,
    to_folder,
):
    rm_tmp_folder(to_folder)
    loaded_tasks = list()
    os.mkdir(to_folder)
    for task_name in load_task_names(
        from_folder, get_list_env("ENABLED_TASKS"), get_list_env("DISABLED_TASKS")
    ):
        with open(os.path.join(to_folder, f"{task_name}.py"), "w") as f:
            f.write(
                task_file_template.format(
                    task_name=task_name,
                    soft_time_limit=os.environ["SOFT_TIME_LIMIT"],
                    time_limit=os.environ["TIME_LIMIT"],
                ),
            )

        loaded_tasks.append(task_name)
    return loaded_tasks
