FROM irid/py3

RUN apt update
RUN apt install redis-server
RUN pip install -r req.txt

COPY fasttask /fasttask
WORKDIR /fasttask

CMD celery -A celery_tasks worker -l info