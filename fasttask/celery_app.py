from celery import Celery
import os

app = Celery(
    'celery_task',
    broker='redis://redis:6379/1',
    backend='redis://redis:6379/2',
    include=[
        f'tasks.{py_file[:-3]}' for py_file in os.listdir("tasks") if py_file.endswith(".py")
    ],
    result_extended=True,
)

# Optional configuration, see the application user guide.
app.conf.update(
    result_expires=3600,
)

if __name__ == '__main__':
    app.start()
