import os
import shutil


def get_bool_env(name):
    return os.environ.get(name, "False") == "True"


def get_int_env(name, default=0):
    return int(os.environ.get(name, default))


def get_list_env(name):
    return [s.strip() for s in os.environ.get(name, "").split(",") if s.strip()]


def load_task_names(folder_path, use_filter=True):

    if use_filter:
        enabled_tasks = get_list_env("enabled_tasks")
        disabled_tasks = get_list_env("disabled_tasks")

    task_names = set()
    for task_name in [py_file[:-3] for py_file in os.listdir(folder_path) if py_file.endswith(".py")]:
        if use_filter:
            if enabled_tasks and task_name not in enabled_tasks:
                continue
            if disabled_tasks and task_name in disabled_tasks:
                continue
        task_names.add(task_name)
    return task_names


def rm_tmp_folder(folder_path):
    if os.path.exists(folder_path):
        shutil.rmtree(folder_path)
