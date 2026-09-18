#!/bin/bash
# 启动内嵌的 S3 兼容对象存储（versitygw，posix 后端）。
#
# 凭据必须与 utils/result_storage.py::derive_s3_credentials 保持一致：
# 未显式配置 S3_ACCESS_KEY / S3_SECRET_KEY 时，由集群信任根 TASK_QUEUE_PASSWD
# 派生。这样 master 与所有 worker 各自算出同一份凭据，部署方无需额外配置。

set -e

if [ "$RESULT_TYPE" != "S3" ] && [ "$RESULT_TYPE" != "AUTO" ]; then
    echo "Result storage mode is '${RESULT_TYPE:-JSON}', S3 server not started."
    exit 0
fi

if [ -n "$S3_ACCESS_KEY" ] && [ -n "$S3_SECRET_KEY" ]; then
    export ROOT_ACCESS_KEY="$S3_ACCESS_KEY"
    export ROOT_SECRET_KEY="$S3_SECRET_KEY"
else
    export ROOT_ACCESS_KEY="fasttask"
    # 与 Python 侧 hashlib.sha256(f"fasttask:s3:{secret}").hexdigest() 等价
    export ROOT_SECRET_KEY="$(printf 'fasttask:s3:%s' "$TASK_QUEUE_PASSWD" | sha256sum | awk '{print $1}')"
fi

data_dir="${S3_DATA_DIR:-/fasttask/files/fasttask/s3}"
mkdir -p "$data_dir/buckets" "$data_dir/versions"

echo "Starting versitygw on :${S3_PORT:-9000} (data dir: $data_dir)"

exec versitygw \
    --port ":${S3_PORT:-9000}" \
    posix \
    --versioning-dir "$data_dir/versions" \
    "$data_dir/buckets"
