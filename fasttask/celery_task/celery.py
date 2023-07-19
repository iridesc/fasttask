from __future__ import absolute_import, unicode_literals
from celery import Celery

app = Celery(
    'celery_task',
    broker='redis://redis:6379/1',
    backend='redis://redis:6379/2',
    include=[
        'celery_task.add',
    ],
    result_extended=True,
)

# Optional configuration, see the application user guide.
app.conf.update(
    result_expires=3600,
)

if __name__ == '__main__':
    app.start()
