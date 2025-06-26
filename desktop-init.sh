#!/bin/bash

nohup xfce4-terminal -x bash -c "sleep 30; cd /fasttask && python3 run.py; bash" > /var/log/desktop_init.log 2>&1 &