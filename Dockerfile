FROM irid/py3

COPY fasttask/req.txt req.txt
RUN pip install -r req.txt

COPY fasttask /fasttask
WORKDIR /fasttask

CMD celery multi start w1 -A celery_task -l info && uvicorn api:app --host 0.0.0.0 --port 80 --reload