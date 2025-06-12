#!/bin/bash

export FASTTASK_DIR=/fasttask
export FILES_DIR=$FASTTASK_DIR/files
export FASTTASK_FILES_DIR=$FILES_DIR/fasttask
export LOG_DIR=$FASTTASK_FILES_DIR/log
export CONF_DIR=$FASTTASK_FILES_DIR/conf
export SSL_CERT_DIR=$FASTTASK_FILES_DIR/ssl_cert
export SSL_KEYFILE=$SSL_CERT_DIR/key.pem
export SSL_CERTFILE=$SSL_CERT_DIR/cert.pem
export REDIS_DIR=$FASTTASK_FILES_DIR/redis
export CELERY_DIR=$FASTTASK_FILES_DIR/celery
export TASK_QUEUE_PORT=6379


export LOADED_TASKS=$(python load_tasks.py)
if [ -z "$UVICORN_WORKERS" ]; then
    export UVICORN_WORKERS=$(grep -c ^processor /proc/cpuinfo)
fi

if [ -z "$RDB_SNAPSHOT_GAP" ]; then
    export RDB_SNAPSHOT_GAP=60
fi

create_dir_if_not_exists_or_exit() {
    local dir_path="$1"

    if [ -d "$dir_path" ]; then
        echo "folder exist. '$dir_path' "
    else
        mkdir -p "$dir_path"

        if [ $? -ne 0 ]; then
            echo "folder create error. '$dir_path'" >&2
            exit 1
        else
            echo "folder created. '$dir_path'"
        fi
    fi
}

create_dir_if_not_exists_or_exit "$FASTTASK_FILES_DIR"
create_dir_if_not_exists_or_exit "$LOG_DIR"
create_dir_if_not_exists_or_exit "$SSL_CERT_DIR"
create_dir_if_not_exists_or_exit "$REDIS_DIR"
create_dir_if_not_exists_or_exit "$CELERY_DIR"
create_dir_if_not_exists_or_exit "$CONF_DIR"

generate_ssl_certs() {
    if [ ! -f "$SSL_KEYFILE" ] || [ ! -f "$SSL_CERTFILE" ]; then
        echo "---->> Generating self-signed SSL certificates..."
        openssl req -x509 -nodes -newkey rsa:4096 -keyout "$SSL_KEYFILE" -out "$SSL_CERTFILE" -days 365 -subj "/CN=localhost"
        echo "---->> openssl command exit code: $?"

        if [ $? -ne 0 ]; then
            echo "---->> Error: Failed to generate SSL certificates."
            exit 1
        fi

        echo "---->> SSL certificates generated successfully in $SSL_CERT_DIR"
    else
        echo "---->> SSL certificates already exist in $SSL_CERT_DIR"
    fi
}

echo "
                                 /\\_/\\
                                ( o.o )
                                > ^ <   F A S T T A S K
                             ///|||||\\\\\\
                            ⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡
                            >>  B O O T I N G  U P  <<
                            ⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡⚡
"

echo ""
echo "---->> LOADED_TASKS=$LOADED_TASKS"
echo "---->> RESULT_EXPIRES=$RESULT_EXPIRES"
echo "---->> NODE_TYPE=$NODE_TYPE"
echo "---->> WORKER_POOL=$WORKER_POOL"
echo "---->> WORKER_CONCURRENCY=$WORKER_CONCURRENCY"
echo "---->> CPU_COUNT=$CPU_COUNT"
echo "---->> UVICORN_WORKERS=$UVICORN_WORKERS"

if [ "$NODE_TYPE" = "single_node" ]; then
    export MASTER_HOST=127.0.0.1
    export TASK_QUEUE_PASSWD=passwd
    generate_ssl_certs
    echo "---->> Starting supervisord with single_node configuration..."
    exec supervisord -c "/fasttask/supervisord_${NODE_TYPE}.conf"

elif [ "$NODE_TYPE" = "distributed_master" ]; then
    echo "---->> Starting supervisord with distributed_master configuration..."
    generate_ssl_certs
    exec supervisord -c "/fasttask/supervisord_${NODE_TYPE}.conf"

elif [ "$NODE_TYPE" = "distributed_worker" ]; then
    echo "---->> Starting supervisord with distributed_master configuration..."
    exec supervisord -c "/fasttask/supervisord_${NODE_TYPE}.conf"

else
    echo "---->> Error: Unsupported NODE_TYPE: $NODE_TYPE. Please set a valid value and try again."
    exit 1

fi
