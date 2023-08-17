import os


def load_task_names():
    return [py_file[:-3] for py_file in os.listdir("tasks") if py_file.endswith(".py")]
