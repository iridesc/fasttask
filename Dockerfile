FROM python:latest

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends redis-server supervisor && \
    rm -rf /var/lib/apt/lists/*

COPY fasttask/requirements.txt /
RUN pip install --no-cache-dir -r requirements.txt

COPY fasttask /fasttask
WORKDIR /fasttask