#!/bin/bash

# 如果设置了 -e，脚本中任何命令失败都会退出
set -e

# 检查环境变量
if [ "$FLOWER_ENABLED" = "True" ]; then
    echo "Starting Flower service on port $FLOWER_PORT..."
    
    # 使用 exec 确保 Celery 进程接管 PID 1 (在容器中) 或直接接收 Supervisor 信号
    exec celery -A celery_app flower \
        --address=0.0.0.0 \
        --port="${FLOWER_PORT:-5555}" \
        --url_prefix=flower \
        --max_tasks="${FLOWER_MAX_TASKS:-3000}"
else
    echo "Flower is disabled via FLOWER_ENABLED."
    # 正常退出，让 Supervisor 知道这是预期的停止
    exit 0
fi