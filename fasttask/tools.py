import os
import shutil


def strip_task_names(task_names_str):
    return {task_name.strip() for task_name in task_names_str.split(",") if task_name.strip()}


def load_task_names(folder_path):
    enabled_tasks = strip_task_names(os.environ.get("enabled_tasks", ""))
    disabled_tasks = strip_task_names(os.environ.get("disabled_tasks", ""))

    print(f"enabled_tasks={enabled_tasks}")
    print(f"disabled_tasks={disabled_tasks}")

    need_load_task_names = set()
    for task_name in [py_file[:-3] for py_file in os.listdir(folder_path) if py_file.endswith(".py")]:
        if enabled_tasks and task_name not in enabled_tasks:
            continue
        if disabled_tasks and task_name in disabled_tasks:
            continue
        need_load_task_names.add(task_name)
    if not need_load_task_names:
        raise Exception("no task need load!")
    print(f"{need_load_task_names=}")

    return need_load_task_names


def rm_tmp_folder(folder_path):
    if os.path.exists(folder_path):
        shutil.rmtree(folder_path)
