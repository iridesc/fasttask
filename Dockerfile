FROM irid/py3

COPY fasttask /fasttask
WORKDIR /fasttask
RUN pip install -r req.txt

CMD celery multi start w1 -A celery_task -l info && uvicorn api:app --host 0.0.0.0 --port 80 --reload