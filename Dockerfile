FROM python:slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends redis-server supervisor && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY tini/tini-amd64 /usr/local/bin/tini
RUN chmod +x /usr/local/bin/tini

COPY fasttask/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

COPY fasttask /fasttask
WORKDIR /fasttask

ENTRYPOINT ["/usr/local/bin/tini", "--"]
CMD ["python", "run.py"]