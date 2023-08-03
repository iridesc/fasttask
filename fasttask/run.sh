#!/bin/bash

nohup celery -A celery_app worker --loglevel=info >/var/log/celery.log 2>&1 &
nohup uvicorn api:app --host 0.0.0.0 --port 80 --reload >/var/log/uvicorn.log 2>&1 &
tail -f /var/log/celery.log /var/log/uvicorn.log /var/log/celery/*.log
