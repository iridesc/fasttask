#!/bin/bash

cd /fasttask

python load_tasks.py
# 检查环境变量node_type是否已设置
if [ -z "$node_type" ]; then
    echo "Error: The environment variable 'node_type' is not set. Please set it before running this script."
    exit 1

# 检查环境变量node_type是否已设置
elif [ -z "$rdb_snapshot_gap" ]; then
    echo "Error: The environment variable 'rdb_snapshot_gap' is not set. Please set it before running this script."
    exit 1

elif [ "$node_type" = "single_node" ]; then
    echo "node_type: single_node"
    export master_host=127.0.0.1
    export task_queue_port=6379
    export task_queue_passwd=passwd
    echo "Starting Redis on $master_host:$task_queue_port..."
    nohup redis-server --bind "$master_host" --requirepass "$task_queue_passwd" --port "$task_queue_port" --save $rdb_snapshot_gap 1 >/var/log/redis.log 2>&1 &
    nohup celery -A celery_app worker --loglevel=info >/var/log/celery.log 2>&1 &
    nohup uvicorn api:app --host 0.0.0.0 --port 80 --reload >/var/log/uvicorn.log 2>&1 &
    tail -f /var/log/redis.log /var/log/celery.log /var/log/uvicorn.log /var/log/celery/*.log

elif [ "$node_type" = "distributed_master" ]; then
    echo "node_type: distributed_master"
    echo "Starting Redis on all interfaces (0.0.0.0)..."
    export master_host=0.0.0.0
    export task_queue_port=6379
    nohup redis-server --bind "$master_host" --requirepass "$task_queue_passwd" --port "$task_queue_port" --save $rdb_snapshot_gap 1 >/var/log/redis.log 2>&1 &
    nohup uvicorn api:app --host 0.0.0.0 --port 80 --reload >/var/log/uvicorn.log 2>&1 &
    tail -f /var/log/redis.log /var/log/celery.log /var/log/uvicorn.log /var/log/celery/*.log

elif [ "$node_type" = "distributed_worker" ]; then
    echo "node_type: distributed_worker"
    echo "Starting celery workers..."
    nohup celery -A celery_app worker --loglevel=info >/var/log/celery.log 2>&1 &
    tail -f /var/log/celery.log /var/log/celery/*.log

else
    echo "Error: Unsupported node_type: $node_type. Please set a valid value and try again."
    exit 1

fi
