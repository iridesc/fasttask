import os
import shutil

task_file_template = """import sys
from celery_app import app

sys.path.append("tasks")
from utils.redis_lock import RedisConcurrencyController
from tasks.{task_name} import {task_name}


@app.task( soft_time_limit={soft_time_limit}, time_limit={time_limit})
def _{task_name}(*args, **kwargs):
    concurrency_params = kwargs.get('concurrency_params')
    if concurrency_params:
        concurrency_key=concurrency_params['concurrency_key']
        max_concurrency = concurrency_params['max_concurrency']
        expire = concurrency_params['expire']
        controller = RedisConcurrencyController(max_concurrent=max_concurrency, expire=expire)
        if controller.acquire(concurrency_key):
            try:
                return {task_name}(*args, **kwargs)
            finally:
                controller.release(resource)
        else:
            countdown = concurrency_params['countdown']
            raise self.retry(countdown=countdown)
    else:
        return {task_name}(*args, **kwargs)
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
        from_folder, get_list_env("ENABLED_TASKS"), get_list_env("ENABLED_TASKS")
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
