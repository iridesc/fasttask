from __future__ import absolute_import, unicode_literals
from celery import Celery

app = Celery('celery_tasks',
             broker='redis://localhost:6379/1',
             backend='redis://localhost:6379/2',
             include=['celery_tasks.tasks'])

# Optional configuration, see the application user guide.
app.conf.update(
    result_expires=3600,
)

if __name__ == '__main__':
    app.start()
