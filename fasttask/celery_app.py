from celery import Celery
from tools import load_task_names
app = Celery(
    'celery_task',
    broker='redis://redis:6379/1',
    backend='redis://redis:6379/2',
    include=[
        f'tasks.{task_name}' for task_name in load_task_names()
    ],
    result_extended=True,
)

# Optional configuration, see the application user guide.
app.conf.update(
    result_expires=3600,
)

if __name__ == '__main__':
    app.start()
