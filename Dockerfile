FROM irid/py3

ENV PYTHONDONTWRITEBYTECODE=1
RUN ln -sf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime

RUN apt update
RUN apt install -y redis-server

COPY req.txt req.txt
RUN pip install -r req.txt

COPY fasttask /fasttask
WORKDIR /fasttask
