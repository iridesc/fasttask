#!/bin/bash
SSL_CERT_DIR="./files/ssl_cert"
SSL_KEYFILE="$SSL_CERT_DIR/key.pem"
SSL_CERTFILE="$SSL_CERT_DIR/cert.pem"

get_rdb_snapshot_param() {
    if [ -z "$rdb_snapshot_gap" ]; then
        echo ""

    else
        echo "--save $rdb_snapshot_gap 1"
    fi
}

# Function to generate SSL certificates if they don't exist
generate_ssl_certs() {
    # Create the SSL certificate directory if it doesn't exist
    if [ ! -d "$SSL_CERT_DIR" ]; then
        echo "---->> Creating SSL certificate directory: $SSL_CERT_DIR"
        mkdir -p "$SSL_CERT_DIR"
    fi

    # Check if the SSL key and certificate files exist
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

cd /fasttask

loaded_tasks=$(python load_tasks.py)
echo ""
echo ">>>>>>>>>>>>>>>>>>>      ..      <<<<<<<<<<<<<<<<<<<"
echo ">>>>>>>>>>>>>>>>>>>     ....     <<<<<<<<<<<<<<<<<<<"
echo ">>>>>>>>>>>>>>>>>>>   FastTask   <<<<<<<<<<<<<<<<<<<"
echo ">>>>>>>>>>>>>>>>>>>  ..........  <<<<<<<<<<<<<<<<<<<"
echo ">>>>>>>>>>>>>>>>>>> ............ <<<<<<<<<<<<<<<<<<<"

echo ""
echo "---->> loaded_tasks=$loaded_tasks"
echo "---->> result_expires=$result_expires"
echo "---->> node_type=$node_type"
echo "---->> worker_pool=$worker_pool"
echo "---->> worker_concurrency=$worker_concurrency"

if [ "$node_type" = "single_node" ]; then

    rdb_snapshot_param=$(get_rdb_snapshot_param)
    echo "---->> rdb_snapshot_param: $rdb_snapshot_param"

    export master_host=127.0.0.1
    export task_queue_port=6379
    export task_queue_passwd=passwd

    echo "---->> Starting Redis on $master_host:$task_queue_port..."
    nohup redis-server --bind "$master_host" --requirepass "$task_queue_passwd" --port "$task_queue_port" $rdb_snapshot_param >/var/log/redis.log 2>&1 &
    nohup celery -A celery_app worker --queues "$loaded_tasks" --loglevel=info >/var/log/celery.log 2>&1 &

    # Generate SSL certificates if they don't exist
    generate_ssl_certs

    echo "---->> Starting Uvicorn with HTTPS..."
    nohup uvicorn api:app --host 0.0.0.0 --port 443 --reload --ssl-keyfile "$SSL_KEYFILE" --ssl-certfile "$SSL_CERTFILE" >/var/log/uvicorn.log 2>&1 &

    echo ""
    sleep 3
    tail -f -n 100 /var/log/celery.log /var/log/uvicorn.log /var/log/redis.log

elif [ "$node_type" = "distributed_master" ]; then
    echo "---->> Starting Redis on all interfaces (0.0.0.0)..."

    rdb_snapshot_param = $(get_rdb_snapshot_param)

    export master_host=0.0.0.0
    export task_queue_port=6379

    nohup redis-server --bind "$master_host" --requirepass "$task_queue_passwd" --port "$task_queue_port" $rdb_snapshot_param >/var/log/redis.log 2>&1 &

    # Generate SSL certificates if they don't exist
    generate_ssl_certs

    echo "---->> Starting Uvicorn with HTTPS..."
    nohup uvicorn api:app --host 0.0.0.0 --port 443 --reload --ssl-keyfile "$SSL_KEYFILE" --ssl-certfile "$SSL_CERTFILE" >/var/log/uvicorn.log 2>&1 &

    echo ""
    sleep 3
    tail -f -n 100 /var/log/celery.log /var/log/uvicorn.log /var/log/redis.log

elif [ "$node_type" = "distributed_worker" ]; then
    echo "---->> Starting celery workers..."
    nohup celery -A celery_app worker --queues "$loaded_tasks" --loglevel=info >/var/log/celery.log 2>&1 &

    echo ""
    sleep 3
    tail -f -n 100 /var/log/celery.log

else
    echo "---->> Error: Unsupported node_type: $node_type. Please set a valid value and try again."
    exit 1

fi
