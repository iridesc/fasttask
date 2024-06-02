FROM irid/py3

ENV PYTHONDONTWRITEBYTECODE=1
RUN apt update && apt install -y redis-server

COPY /fasttask/req.txt /fasttask/req.txt
RUN pip install -r /fasttask/req.txt

COPY fasttask /fasttask
WORKDIR /fasttask
