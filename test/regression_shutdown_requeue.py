#!/usr/bin/env python3
"""优雅关机回归测试：停止容器时正在运行的任务必须被放回队列，重启后从头重跑。

为什么需要这个测试
------------------
这个行为横跨 Celery / Kombu / Billiard 三个库的边界，任何一方升级或配置改动都可能
静默破坏它，而失败模式是「任务被 ack 丢弃、重启后永不重跑」——线上极难发现。
已知的三个破坏点：

1. **billiard >= 4.3.0**：worker 子进程收到 SIGTERM 时会把 ``SystemExit`` 当作「任务结果」
   上报给主进程；Celery 只对 ``Terminated`` / ``WorkerLostError`` 做重投处理，
   ``SystemExit`` 会落到 ``acks_on_failure_or_timeout`` 分支 → ``acknowledge()`` → 消息被丢弃。
2. **supervisord 缺 ``stopsignal=QUIT``**：走 warm shutdown，只会干等任务跑完，随后被强杀，
   消息留在 unacked 里等 ``VISIBILITY_TIMEOUT``（默认约 24 小时）。
3. **``stopwaitsecs`` >= 容器停止超时**：外层 SIGKILL 先到，requeue 与 redis 优雅落盘被打断。

所以升级 celery / kombu / billiard，或改动 ``supervisord_template_conf/celery.conf`` 之后，
都应该跑一遍本脚本。

它做了什么
----------
在临时目录生成 compose.yaml + 一个长睡眠的测试任务（挂载进容器，不进仓库），然后：

1. 起 single_node，下发长任务，确认它真的在 worker 上运行；
2. 按容器停止超时停止容器，记录耗时；
3. 断言日志出现 ``Restoring N unacknowledged message(s)``；
4. 重启容器，断言**同一个任务**从头重跑（用 probe_id 关联）。

用法
----
::

    podman build -t localhost/fasttask:regression .          # 脚本不负责构建镜像
    python3 test/regression_shutdown_requeue.py --image localhost/fasttask:regression

常用参数：

* ``--image``        被测镜像，默认 ``docker.io/irid/fasttask:test``
* ``--runtime``      ``podman`` / ``docker``，默认按探测到的 compose 命令推导
* ``--port``         API 映射端口，默认 19099（避开常规实例的 9001）
* ``--stop-timeout`` 容器停止超时，默认 10，应与实际部署保持一致
* ``--task-seconds`` 测试任务睡眠时长，默认 600（远大于测试本身耗时即可）
* ``--keep``         失败时保留容器与临时目录，便于排查
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid

import requests
import urllib3

urllib3.disable_warnings()

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(HERE)
TASK_NAME = "sleep_probe"

# 由脚本生成并挂载进容器的测试任务：刻意打印 probe_id，用来确认重跑的是同一个任务
PROBE_TASK_SRC = '''"""回归测试用的长睡眠任务（由 test/regression_shutdown_requeue.py 生成，不进仓库）。"""

import os
import time
from datetime import datetime, timezone

from pydantic import BaseModel


class Params(BaseModel):
    seconds: int = 600
    probe_id: str = ""


class Result(BaseModel):
    probe_id: str
    started_at: str
    finished_at: str


def {task_name}(seconds=600, probe_id=""):
    started_at = datetime.now(timezone.utc).isoformat()
    print(
        f"[{task_name}] START probe_id={{probe_id}} "
        f"pid={{os.getpid()}} at {{started_at}}",
        flush=True,
    )
    time.sleep(seconds)
    finished_at = datetime.now(timezone.utc).isoformat()
    print(f"[{task_name}] DONE probe_id={{probe_id}} at {{finished_at}}", flush=True)
    return {{
        "probe_id": probe_id,
        "started_at": started_at,
        "finished_at": finished_at,
    }}
'''

COMPOSE_TEMPLATE = """services:
  fasttask:
    image: {image}
    container_name: {container_name}
    ports:
      - "{port}:443"
    volumes:
      - {workdir}/files:/fasttask/files
      - {workdir}/{task_name}.py:/fasttask/tasks/{task_name}.py:ro
    environment:
      - NODE_TYPE=single_node
      - FLOWER_ENABLED=False
      - RESULT_TYPE=JSON
"""


class TestFailure(Exception):
    """测试断言失败。"""


def log(msg):
    print(f"[regression] {msg}", flush=True)


def run(cmd, timeout=120, check=True):
    """执行命令并返回 CompletedProcess（stdout/stderr 合并）。"""
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if check and proc.returncode != 0:
        raise TestFailure(
            f"命令失败 {cmd}:\n rc={proc.returncode}\n{proc.stdout}\n{proc.stderr}"
        )
    return proc


def detect_compose():
    """探测可用的 compose 命令，返回 (compose_cmd, container_runtime)。"""
    candidates = [
        (["podman-compose"], "podman"),
        (["docker", "compose"], "docker"),
        (["podman", "compose"], "podman"),
        (["docker-compose"], "docker"),
    ]
    for cmd, runtime in candidates:
        if shutil.which(cmd[0]) is None:
            continue
        try:
            proc = subprocess.run(
                cmd + ["version"], capture_output=True, text=True, timeout=60
            )
        except Exception:  # noqa: BLE001 - 探测失败就换下一个候选
            continue
        if proc.returncode == 0:
            return cmd, runtime
    raise TestFailure("未找到可用的 compose 命令（podman-compose / docker compose）")


def ensure_image(runtime, image):
    proc = run([runtime, "image", "exists", image], check=False)
    if proc.returncode != 0:
        raise TestFailure(
            f"镜像不存在: {image}\n"
            f"请先构建，例如：\n"
            f"  {runtime} build -t {image} {REPO_ROOT}"
        )


class Environment:
    """被测环境的生命周期管理：临时目录、compose 文件、容器启停与日志。"""

    def __init__(self, args, compose_cmd, runtime):
        self.args = args
        self.compose_cmd = compose_cmd
        self.runtime = runtime
        self.workdir = tempfile.mkdtemp(prefix="fasttask-shutdown-regression-")
        self.compose_file = os.path.join(self.workdir, "compose.yaml")
        self.container_name = f"fasttask-shutdown-regression-{args.port}"
        self.compose_project = f"fasttask-regression-{args.port}"
        self.api = f"https://127.0.0.1:{args.port}"
        self._started = False

    # ---------- 准备与清理 ----------

    def prepare(self):
        os.makedirs(os.path.join(self.workdir, "files"), exist_ok=True)
        with open(
            os.path.join(self.workdir, f"{TASK_NAME}.py"), "w", encoding="utf-8"
        ) as f:
            f.write(PROBE_TASK_SRC.format(task_name=TASK_NAME))
        with open(self.compose_file, "w", encoding="utf-8") as f:
            f.write(
                COMPOSE_TEMPLATE.format(
                    image=self.args.image,
                    container_name=self.container_name,
                    port=self.args.port,
                    workdir=self.workdir,
                    task_name=TASK_NAME,
                )
            )
        log(f"临时目录: {self.workdir}")

    def _compose(self, *args, check=True, timeout=180):
        return run(
            self.compose_cmd
            + ["-f", self.compose_file, "-p", self.compose_project]
            + list(args),
            check=check,
            timeout=timeout,
        )

    def up(self):
        # 清掉上一次的残留，避免 container_name 冲突
        self._compose("down", "-v", check=False, timeout=120)
        run([self.runtime, "rm", "-f", self.container_name], check=False)
        self._compose("up", "-d")
        self._started = True

    def cleanup(self):
        log("清理容器与临时目录 ...")
        self._compose("down", "-v", check=False, timeout=120)
        run([self.runtime, "rm", "-f", self.container_name], check=False)
        if not self.args.keep:
            shutil.rmtree(self.workdir, ignore_errors=True)

    # ---------- 观测 ----------

    def logs(self):
        proc = run(
            [self.runtime, "logs", self.container_name], check=False, timeout=120
        )
        return (proc.stdout or "") + (proc.stderr or "")

    def stop(self):
        """按容器停止超时停止容器，返回耗时（秒）。"""
        start = time.monotonic()
        run(
            [
                self.runtime,
                "stop",
                "-t",
                str(self.args.stop_timeout),
                self.container_name,
            ],
            timeout=self.args.stop_timeout + 60,
        )
        return time.monotonic() - start

    def start(self):
        run([self.runtime, "start", self.container_name], timeout=120)

    # ---------- API 交互 ----------

    def api_ready(self):
        try:
            resp = requests.post(
                f"{self.api}/status_info",
                json={"fields": []},
                verify=False,
                timeout=5,
            )
            return resp.status_code == 200
        except Exception:  # noqa: BLE001 - 未起来就是连接失败
            return False

    def wait_api_ready(self, timeout=180):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.api_ready():
                return
            time.sleep(2)
        raise TestFailure(f"API 在 {timeout}s 内未就绪（{self.api}/status_info）")

    def active_task_ids(self):
        resp = requests.post(
            f"{self.api}/status_info",
            json={"fields": ["worker_status"]},
            verify=False,
            timeout=10,
        )
        resp.raise_for_status()
        worker_status = resp.json().get("worker_status") or {}
        details = worker_status.get("worker_details") or {}
        ids = []
        for info in details.values():
            ids.extend(info.get("active_tasks") or [])
        return ids

    def create_task(self, probe_id):
        resp = requests.post(
            f"{self.api}/create/{TASK_NAME}",
            json={"seconds": self.args.task_seconds, "probe_id": probe_id},
            verify=False,
            timeout=30,
        )
        resp.raise_for_status()
        payload = resp.json()
        if not payload.get("id"):
            raise TestFailure(f"创建任务失败: {payload}")
        return payload["id"]

    # ---------- 等待 ----------

    def wait_task_running(self, result_id, timeout=120):
        """等待任务真的被某个 worker 接管（active_tasks 里出现该 id）。"""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if result_id in self.active_task_ids():
                return
            time.sleep(2)
        raise TestFailure(
            f"任务 {result_id} 在 {timeout}s 内没有出现在任何 worker 的 active_tasks 中"
        )

    def wait_probe_started(self, probe_id, min_count=1, timeout=120):
        """等待日志里出现 min_count 次该 probe_id 的 START。"""
        deadline = time.monotonic() + timeout
        pattern = re.compile(rf"\[{TASK_NAME}\] START probe_id={re.escape(probe_id)}")
        while time.monotonic() < deadline:
            count = len(pattern.findall(self.logs()))
            if count >= min_count:
                return count
            time.sleep(2)
        raise TestFailure(
            f"日志里 probe_id={probe_id} 的 START 出现次数不足 {min_count} 次"
        )


def check_image_baseline(env):
    """先确认容器内确实是「改后」的配置，避免拿旧镜像测出假结果。"""
    proc = run(
        [
            env.runtime,
            "exec",
            env.container_name,
            "grep",
            "-E",
            "^(stopsignal|stopwaitsecs)=",
            "/fasttask/supervisord_conf/celery.conf",
        ],
        check=False,
    )
    conf = (proc.stdout or "").strip()
    log(f"容器内 celery.conf:\n{conf}")
    if "stopsignal=QUIT" not in conf:
        raise TestFailure(
            "容器里的 supervisord celery.conf 没有 stopsignal=QUIT —— "
            "这是旧镜像（或被 supervisord_conf/ 覆盖），本测试无意义。"
            "请用包含本次改动的镜像。"
        )


def main():
    parser = argparse.ArgumentParser(
        description="优雅关机 requeue 回归测试（详见脚本头部说明）"
    )
    parser.add_argument(
        "--image", default="docker.io/irid/fasttask:test", help="被测镜像"
    )
    parser.add_argument(
        "--runtime", default=None, help="podman / docker，默认按 compose 命令推导"
    )
    parser.add_argument("--port", type=int, default=19099, help="API 映射端口")
    parser.add_argument(
        "--stop-timeout", type=int, default=10, help="容器停止超时（秒）"
    )
    parser.add_argument(
        "--task-seconds", type=int, default=600, help="测试任务睡眠时长（秒）"
    )
    parser.add_argument("--keep", action="store_true", help="失败时保留环境")
    args = parser.parse_args()

    compose_cmd, detected_runtime = detect_compose()
    runtime = args.runtime or detected_runtime
    log(f"compose 命令: {' '.join(compose_cmd)}")
    log(f"容器运行时: {runtime}")
    log(f"被测镜像: {args.image}")

    ensure_image(runtime, args.image)

    env = Environment(args, compose_cmd, runtime)
    env.prepare()
    probe_id = uuid.uuid4().hex[:12]
    failed = False
    try:
        log("启动 single_node ...")
        env.up()
        env.wait_api_ready()
        check_image_baseline(env)

        log("下发长任务 ...")
        result_id = env.create_task(probe_id)
        log(f"result_id = {result_id}")
        env.wait_task_running(result_id)
        log("任务已在 worker 上运行 ✅")

        log(f"停止容器（-t {args.stop_timeout}）...")
        elapsed = env.stop()
        log(f"停止耗时 {elapsed:.2f}s")
        if elapsed >= args.stop_timeout:
            raise TestFailure(
                f"停止耗时 {elapsed:.2f}s 已达到容器停止超时 {args.stop_timeout}s："
                "requeue 大概率被外层 SIGKILL 打断"
            )

        logs = env.logs()
        restored = re.findall(r"Restoring (\d+) unacknowledged message", logs)
        if not restored:
            raise TestFailure(
                "停止日志里没有 'Restoring N unacknowledged message(s)' —— "
                "消息没有被放回队列（会被 unacked 一直挂着，或已被 ack 丢弃）。"
            )
        log(f"requeue 生效: Restoring {restored[0]} unacknowledged message(s) ✅")

        log("重启容器，等待任务自动重跑 ...")
        env.start()
        count = env.wait_probe_started(probe_id, min_count=2, timeout=180)
        log(f"重启后任务已从头重跑（START 次数 = {count}）✅")

        log("PASS：优雅关机会把运行中的任务放回队列，重启后自动重跑。")
    except (TestFailure, requests.RequestException, subprocess.TimeoutExpired) as exc:
        failed = True
        log(f"FAIL：{exc}")
        log("---- 容器日志尾部（诊断用）----")
        try:
            print("\n".join(env.logs().splitlines()[-60:]), flush=True)
        except Exception:  # noqa: BLE001 - 诊断信息尽力而为
            pass
    finally:
        if failed and args.keep:
            log(f"--keep 已指定，保留环境：{env.workdir}")
            log(f"手动清理：{env.compose_cmd} -f {env.compose_file} down -v")
        else:
            env.cleanup()

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
