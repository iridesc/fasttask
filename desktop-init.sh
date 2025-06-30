#!/bin/bash

launch_with_retry() {
    while true; do
        # 启动终端窗口并运行 run.py
        nohup xfce4-terminal --disable-server --hold -x bash -c "cd /fasttask && python3 run.py; echo '进程退出，按回车关闭...'; read" >/dev/null 2>&1 &
        local TERM_PID=$!

        sleep 5

        if pgrep -f "python3 run.py" >/dev/null; then
            echo "$(date): run.py 启动成功 | 终端PID=$TERM_PID" >>/var/log/desktop_init.log
            wait $TERM_PID
        else
            echo "$(date): run.py 未运行，重启中..." >>/var/log/desktop_init.log
            kill $TERM_PID 2>/dev/null
            sleep 5
        fi
    done
}

nohup bash -c "source '$0'; launch_with_retry" >/var/log/desktop_init.log 2>&1 &
