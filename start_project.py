import os

project_name = input("project name:")
task_name = input("task_name:")

docker_file_content = '''FROM irid/fasttask

COPY req.txt req.txt
RUN pip install -r req.txt

# set up your docker env here if necessary
# ---
'''
docker_compose_content = f'''version: "3.9"

services:
  redis:
    image: redis:alpine
    restart: always
    expose:
      - '6379'

  {project_name}:
    restart: always
    volumes:
      - ./tasks:/fasttask/celery_task/tasks
    command: bash -c "celery multi start w1 -A celery_task -l info && uvicorn api:app --host 0.0.0.0 --port 80 --reload"
    ports:
          - "8000:80"

    image: {project_name}
'''

task_demo_content = f'''from ..celery import app
import time


@app.task
def {task_name}(x, y):
    time.sleep(3)
    return x + y
'''

os.makedirs(project_name)

with open(f"{project_name}/Dockerfile", "w") as f:
    f.write(docker_file_content)

with open(f"{project_name}/docker-compose.yml", "w") as f:
    f.write(docker_compose_content)

with open(f"{project_name}/req.txt", "w") as f:
    f.write("")

os.makedirs(f"{project_name}/tasks")
with open(f"{project_name}/tasks/{task_name}.py", "w") as f:
    f.write(task_demo_content)
