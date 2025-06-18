import os
import shutil
import uuid
from fastapi import HTTPException, status


def get_bool_env(name):
    return os.environ.get(name, "False") == "True"


def get_list_env(name):
    return [s.strip() for s in os.environ.get(name, "").split(",") if s.strip()]


def load_task_names(folder_path, use_filter=True):
    if use_filter:
        ENABLED_TASKS = get_list_env("ENABLED_TASKS")
        DISABLED_TASKS = get_list_env("DISABLED_TASKS")

    task_names = set()
    for task_name in [
        py_file[:-3] for py_file in os.listdir(folder_path) if py_file.endswith(".py")
    ]:
        if use_filter:
            if ENABLED_TASKS and task_name not in ENABLED_TASKS:
                continue
            if DISABLED_TASKS and task_name in DISABLED_TASKS:
                continue
        task_names.add(task_name)
    return task_names


def rm_tmp_folder(folder_path):
    if os.path.exists(folder_path):
        shutil.rmtree(folder_path)


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

