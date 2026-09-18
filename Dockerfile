FROM python:slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

RUN apt-get update && \
    apt-get install -y --no-install-recommends redis-server supervisor curl ca-certificates && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# versitygw：内嵌的 S3 兼容对象存储（Apache-2.0），用于结果外置（RESULT_TYPE=S3/AUTO）
ARG VERSITYGW_VERSION=v1.8.0
RUN set -eux; \
    case "$(dpkg --print-architecture)" in \
        amd64) vgw_arch="x86_64" ;; \
        arm64) vgw_arch="arm64" ;; \
        *) echo "unsupported architecture: $(dpkg --print-architecture)" >&2; exit 1 ;; \
    esac; \
    archive="versitygw_${VERSITYGW_VERSION}_Linux_${vgw_arch}.tar.gz"; \
    curl -fsSL -o /tmp/versitygw.tar.gz \
        "https://github.com/versity/versitygw/releases/download/${VERSITYGW_VERSION}/${archive}"; \
    tar -xzf /tmp/versitygw.tar.gz -C /tmp; \
    mv "/tmp/versitygw_${VERSITYGW_VERSION}_Linux_${vgw_arch}/versitygw" /usr/local/bin/versitygw; \
    chmod +x /usr/local/bin/versitygw; \
    rm -rf /tmp/versitygw.tar.gz "/tmp/versitygw_${VERSITYGW_VERSION}_Linux_${vgw_arch}"; \
    versitygw --version

COPY tini/tini-amd64 /usr/local/bin/tini
RUN chmod +x /usr/local/bin/tini

COPY fasttask/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt && rm /tmp/requirements.txt

COPY fasttask /fasttask
WORKDIR /fasttask

ENTRYPOINT ["/usr/local/bin/tini", "--"]
CMD ["python", "run.py"]