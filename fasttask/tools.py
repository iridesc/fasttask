import os
import shutil


def load_task_names(folder_path):
    return [py_file[:-3] for py_file in os.listdir(folder_path) if py_file.endswith(".py")]


def rm_tmp_folder(folder_path):
    if os.path.exists(folder_path):
        shutil.rmtree(folder_path)
