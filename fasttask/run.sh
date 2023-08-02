#!/bin/bash

celery multi start w1 -A celery_app -l info
nohup uvicorn api:app --host 0.0.0.0 --port 80 --reload >/var/log/uvicorn.log 2>&1 &
tail -f /var/log/uvicorn.log /var/log/celery/*.log
# python
