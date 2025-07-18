FROM python:slim

ENV TINI_VERSION v0.19.0
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl redis-server supervisor && \
    curl -sSL https://github.com/krallin/tini/releases/download/${TINI_VERSION}/tini-amd64 -o /usr/local/bin/tini && \
    chmod +x /usr/local/bin/tini && \
    apt-get remove --purge -y curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY fasttask/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

COPY fasttask /fasttask
WORKDIR /fasttask

ENTRYPOINT ["/usr/local/bin/tini", "--"]
CMD ["python", "run.py"]