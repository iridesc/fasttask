import os
from tools import load_task_names, rm_tmp_folder

task_file_content = """import sys
from celery_app import app

sys.path.append("tasks")

from tasks.task_name import task_name


@app.task
def _task_name(*args, **kwargs):
    return task_name(*args, **kwargs)
"""


def load_tasks(from_folder, to_folder):
    print(f"loading {from_folder=} -> {to_folder=}")

    rm_tmp_folder(to_folder)

    # 创建文件夹
    os.mkdir(to_folder)
    for task_name in load_task_names(from_folder):
        with open(os.path.join(to_folder, f"{task_name}.py"), "w") as f:
            f.write(task_file_content.replace("task_name", task_name))
    print("load_tasks done!")


if __name__ == "__main__":
    loaded_folder = "loaded_tasks"
    load_tasks(from_folder="tasks", to_folder="loaded_tasks")
