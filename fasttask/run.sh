#!/bin/bash
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

SYS_DIR=./files/fasttask
LOG_DIR=$SYS_DIR/log
CONF_DIR=$SYS_DIR/conf
SSL_CERT_DIR=$SYS_DIR/ssl_cert
SSL_KEYFILE="$SSL_CERT_DIR/key.pem"
SSL_CERTFILE="$SSL_CERT_DIR/cert.pem"
REDIS_DIR=$SYS_DIR/redis
CELERY_DIR=$SYS_DIR/celery

create_dir_if_not_exists_or_exit "$SYS_DIR"
create_dir_if_not_exists_or_exit "$LOG_DIR"
create_dir_if_not_exists_or_exit "$SSL_CERT_DIR"
create_dir_if_not_exists_or_exit "$REDIS_DIR"
create_dir_if_not_exists_or_exit "$CELERY_DIR"
create_dir_if_not_exists_or_exit "$CONF_DIR"

get_rdb_snapshot_param() {
    if [ -z "$rdb_snapshot_gap" ]; then
        echo ""

    else
        echo "--save $rdb_snapshot_gap 1 --dir $REDIS_DIR"
    fi
}

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

tail_log() {
    echo ""
    sleep 3
    tail -f -n 100 $LOG_DIR/celery.log
}

if [ -z "$node_type" ]; then
    echo "---->> Error: The environment variable 'node_type' is not set. Please set it before running this script."
    exit 1
fi

if [ -z "$result_expires" ]; then
    echo "---->> Error: The environment variable 'result_expires' is not set. Please set it before running this script."
    exit 1
fi

if [ -z "$worker_pool" ]; then
    echo "---->> Error: The environment variable 'worker_pool' is not set. Please set it before running this script."
    exit 1
fi

if [ -z "$worker_concurrency" ]; then
    echo "---->> Error: The environment variable 'worker_concurrency' is not set. Please set it before running this script."
    exit 1
fi

if [ -z "$uvicorn_workers" ]; then
    uvicorn_workers=$(grep -c ^processor /proc/cpuinfo)
fi

cd /fasttask

loaded_tasks=$(python load_tasks.py)
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
echo "---->> loaded_tasks=$loaded_tasks"
echo "---->> result_expires=$result_expires"
echo "---->> node_type=$node_type"
echo "---->> worker_pool=$worker_pool"
echo "---->> worker_concurrency=$worker_concurrency"
echo "---->> CPU_COUNT=$CPU_COUNT"

if [ "$node_type" = "single_node" ]; then

    rdb_snapshot_param=$(get_rdb_snapshot_param)
    echo "---->> rdb_snapshot_param: $rdb_snapshot_param"

    export master_host=127.0.0.1
    export task_queue_port=6379
    export task_queue_passwd=passwd

    echo "---->> Starting Redis on $master_host:$task_queue_port..."
    nohup redis-server --bind "$master_host" --requirepass "$task_queue_passwd" --port "$task_queue_port" $rdb_snapshot_param >$LOG_DIR/redis.log 2>&1 &
    nohup celery -A celery_app worker --queues "$loaded_tasks" --loglevel=info --statedb="$CELERY_DIR"/worker.state >$LOG_DIR/celery.log 2>&1 &

    # Generate SSL certificates if they don't exist
    generate_ssl_certs

    echo "---->> Starting Uvicorn with HTTPS..."
    nohup uvicorn api:app --host 0.0.0.0 --port 443 --workers "$uvicorn_workers" --ssl-keyfile "$SSL_KEYFILE" --ssl-certfile "$SSL_CERTFILE" >$LOG_DIR/uvicorn.log 2>&1 &
    tail_log

elif [ "$node_type" = "distributed_master" ]; then
    echo "---->> Starting Redis on all interfaces (0.0.0.0)..."

    rdb_snapshot_param = $(get_rdb_snapshot_param)

    export master_host=0.0.0.0
    export task_queue_port=6379

    nohup redis-server --bind "$master_host" --requirepass "$task_queue_passwd" --port "$task_queue_port" $rdb_snapshot_param >$LOG_DIR/redis.log 2>&1 &

    # Generate SSL certificates if they don't exist
    generate_ssl_certs

    echo "---->> Starting Uvicorn with HTTPS..."
    nohup uvicorn api:app --host 0.0.0.0 --port 443 --workers "$uvicorn_workers" --ssl-keyfile "$SSL_KEYFILE" --ssl-certfile "$SSL_CERTFILE" >$LOG_DIR/uvicorn.log 2>&1 &
    tail_log

elif [ "$node_type" = "distributed_worker" ]; then
    echo "---->> Starting celery workers..."
    nohup celery -A celery_app worker --queues "$loaded_tasks" --loglevel=info --statedb="$CELERY_DIR"/worker.state >$LOG_DIR/celery.log 2>&1 &
    tail_log 

else
    echo "---->> Error: Unsupported node_type: $node_type. Please set a valid value and try again."
    exit 1

fi
