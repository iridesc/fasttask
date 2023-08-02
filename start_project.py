import os
import shutil


def get_int_input_or_default(name, default):
    input_str = input(f"{name} (default:{default}):")
    return int(input_str) if input_str else default


def read_file(file):
    with open(file) as f:
        return f.read()


def write_file(content, file):
    with open(file, "w") as f:
        f.write(content)


def replace_file_content(file, replace_dict):
    content = read_file(file)
    content = content.format_map(replace_dict)
    write_file(content, file)


project_name = input("project name:")
port = get_int_input_or_default("port", 80)
worker_amount = get_int_input_or_default("worker_amount", 16)

fasttask_path = os.path.abspath(os.path.dirname(__file__))
print(fasttask_path) 

shutil.copytree(os.path.join(fasttask_path, "fasttask/tasks"), f"{project_name}/tasks")
shutil.copyfile(os.path.join(fasttask_path, "Dockerfile_project"), f"{project_name}/Dockerfile")
shutil.copyfile(os.path.join(fasttask_path, "docker-compose_project.yml"), f"{project_name}/docker-compose.yml")
write_file("\n", f"{project_name}/req.txt")
replace_file_content(f"{project_name}/docker-compose.yml", {"project_name": project_name, "port": port})
