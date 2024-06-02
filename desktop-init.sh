#!/bin/bash

nohup sleep 30 && exec xfce4-terminal -x /fasttask/run.sh > /var/log/desktop_init.log 2>&1 &
