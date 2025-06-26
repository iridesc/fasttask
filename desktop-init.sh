#!/bin/bash
#更改了可能存在的自动执行顺序错误
nohup bash -c 'sleep 30 && xfce4-terminal -x bash -c "cd /fasttask && python3 run.py; bash"' > /var/log/desktop_init.log 2>&1 &