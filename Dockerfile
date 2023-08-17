FROM irid/py3

ENV PYTHONDONTWRITEBYTECODE=1

RUN apt update

COPY req.txt req.txt
RUN pip install -r req.txt

COPY fasttask /fasttask
WORKDIR /fasttask
