#!/usr/bin/env python3
"""打印 FastTask 服务**实际暴露给 AI 的** MCP 描述（离线预览，不需要起服务、不需要客户端）。

为什么要在项目容器里跑
----------------------
instructions 与工具描述由三样东西共同决定，它们在容器里才是完整的：

- ``setting.py`` 的 title/summary/description/version   → instructions 的模块身份
- 每个任务模块的 docstring                              → 该任务工具的业务说明
- 环境变量（``LOADED_TASKS`` / ``API_*`` / ``ENABLED_TASKS``）→ 有哪些任务、注册哪些工具

脚本会**先读运行中 uvicorn 进程的真实环境**（``/proc/<pid>/environ``）—— ``podman exec``
拿到的只是 compose 里写的那几个变量，而 ``API_RUN=False``、``RESULT_TYPE=AUTO``、
``ENABLED_TASKS`` 这些由 ``run.py`` 在启动时补齐的值只有进程里才有。读不到时才退回
当前 shell 的环境，最后用约定默认值兜底。

用法
----
服务在跑（最准，能读到真实服务的环境）：

    podman cp <skill>/scripts/preview_mcp_descriptions.py <容器>:/tmp/
    podman exec -w /fasttask <容器> python /tmp/preview_mcp_descriptions.py

改完还没部署（用基础镜像挂载代码跑一次，不需要重建镜像）：

    podman run --rm \
      -v "$PWD":/project:ro \
      -v "<skill>/scripts":/scripts:ro \
      docker.io/irid/fasttask:latest \
      python /scripts/preview_mcp_descriptions.py \
        --project-dir /project --platform-dir /fasttask

退出码：0 = 描述齐全；1 = 有提醒（缺 docstring / 调用规则被挤出可见窗口等）；2 = 跑不起来。
"""

import argparse
import ast
import glob
import json
import os
import sys
from pathlib import Path

#: 客户端普遍只渲染 instructions 的前几百字符（pi-mcp-adapter 是 300）。
#: 决定调用行为的规则必须落在这个窗口里，否则等于没写。
VISIBLE_WINDOW = 300

#: 这些变量是 import utils.mcp_server 的必要条件，但只有 run.py 会补默认值。
#: podman exec 的环境里没有它们，缺哪个就按平台约定补哪个。
FALLBACK_ENV = {
    "MASTER_HOST": "127.0.0.1",
    "TASK_QUEUE_PORT": "6379",
    "TASK_QUEUE_PASSWD": "preview",
    "WORKER_POOL": "prefork",
    "WORKER_CONCURRENCY": "1",
    "FLOWER_ENABLED": "False",
    "RESULT_EXPIRES": "259200",
    "RESULT_TYPE": "JSON",
    "RESULT_AUTO_TO_S3_SIZE": "1048576",
    "SOFT_TIME_LIMIT": "86400",
    "TIME_LIMIT": "86460",
    "VISIBILITY_TIMEOUT": "86520",
    "CELERY_DIR": "/tmp",
    "LAZY_ACTION_FILE_PATH": "/tmp",
    "CONF_DIR": "/fasttask/files/fasttask/conf",
    "FASTTASK_DIR": "/fasttask",
    "FILES_DIR": "/fasttask/files",
}

#: API_* 默认全开（与 run.py 的 single_node / distributed_master 一致）。
API_FLAGS = (
    "API_CREATE",
    "API_CHECK",
    "API_RUN",
    "API_STATUS_INFO",
    "API_REVOKE",
    "API_MCP",
    "API_FILE_DOWNLOAD",
    "API_FILE_UPLOAD",
    "API_DOCS",
)


def running_process_env():
    """从运行中的 uvicorn 进程读真实环境变量；读不到返回空 dict。

    容器里通常没有 ps/pgrep（python:slim 基础镜像），所以直接扫 /proc。
    """
    for proc in glob.glob("/proc/[0-9]*"):
        try:
            cmdline = Path(proc, "cmdline").read_bytes().decode(errors="replace")
        except OSError:
            continue
        if "uvicorn" not in cmdline:
            continue
        try:
            raw = Path(proc, "environ").read_bytes().decode(errors="replace")
        except OSError:
            continue
        env = {}
        for item in raw.split("\0"):
            key, sep, value = item.partition("=")
            if sep:
                env[key] = value
        if env:
            return env
    return {}


def scan_tasks(task_dir):
    """按平台的规则扫出任务名（LOADED_TASKS 缺失时的兜底）。

    与 utils/tools.load_task_names 同规则：只扫 tasks/*.py，排除 __init__.py，
    并尊重 ENABLED_TASKS / DISABLED_TASKS。
    """
    enabled = [s.strip() for s in os.environ.get("ENABLED_TASKS", "").split(",") if s.strip()]
    disabled = [s.strip() for s in os.environ.get("DISABLED_TASKS", "").split(",") if s.strip()]
    names = []
    for path in sorted(Path(task_dir).glob("*.py")):
        if path.name.startswith("__"):
            continue
        name = path.stem
        if enabled and name not in enabled:
            continue
        if name in disabled:
            continue
        names.append(name)
    return names


def prepare_env(task_dir):
    """把环境补齐到足以 import utils.mcp_server，返回 env 来源说明。"""
    source = "运行中进程"
    env = running_process_env()
    if not env:
        source = "当前 shell"
        env = dict(os.environ)
    else:
        for key, value in os.environ.items():
            env.setdefault(key, value)

    for key, value in FALLBACK_ENV.items():
        env.setdefault(key, value)
    for flag in API_FLAGS:
        env.setdefault(flag, "True")
    env.setdefault("NODE_TYPE", "single_node")
    if not env.get("LOADED_TASKS"):
        env["LOADED_TASKS"] = ",".join(scan_tasks(task_dir))
        source += "（LOADED_TASKS 缺失，按 tasks/ 扫描推出）"

    os.environ.update(env)
    return source
def setup_paths(project_dir, platform_dir):
    """把平台代码与项目代码都摆到 sys.path 上，项目在前。

    容器里两者是同一个目录（/fasttask），所以默认值就是当前目录；
    在宿主机上用基础镜像挂载预览时，两者分开传：

    - ``project_dir``：放 ``setting.py`` 与 ``tasks/``，必须排在前面，
      否则 ``import setting`` 会拿到平台自带的那个示例 setting.py
    - ``platform_dir``：放 ``utils/`` ``celery_app.py``（平台本体）

    "tasks" 子目录也要进 path：任务里常见 ``from packages.x import ...``，
    api.py 启动时也会 append 这一句。
    """
    for path in (platform_dir, project_dir):
        if path and path not in sys.path:
            sys.path.insert(0, path)
    tasks_path = os.path.join(project_dir, "tasks")
    if tasks_path not in sys.path:
        sys.path.append(tasks_path)

    # 平台目录里也有一个示例 tasks/。两个目录在 sys.path 上会组成同一个
    # 命名空间包，而平台那份 tasks/packages 带 __init__.py（常规包）——
    # 常规包会盖掉项目里的命名空间子包，于是 `from tasks.packages.x import y`
    # 报 ModuleNotFoundError。把 tasks 钉死在项目目录上，避免这种串台。
    if os.path.abspath(project_dir) != os.path.abspath(platform_dir):
        import types

        pinned = types.ModuleType("tasks")
        pinned.__path__ = [tasks_path]
        sys.modules["tasks"] = pinned


def collect(project_dir, platform_dir):
    """构建与线上同一套描述的 MCP server，把 instructions 与工具描述取出来。"""
    import asyncio

    # 用 `python /tmp/xx.py` 跑时 sys.path[0] 是脚本所在目录，而 api.py 是被 uvicorn
    # 从工作目录启动的（两者不一致），所以这里显式把项目与平台目录补上。
    setup_paths(project_dir, platform_dir)
    from utils.mcp_server import build_mcp_server
    from utils.task_ops import task_doc

    tasks = [name for name in os.environ["LOADED_TASKS"].split(",") if name.strip()]
    server = build_mcp_server(tasks, lambda: "PREVIEW")
    tools = [
        {"name": tool.name, "description": (tool.description or "").strip()}
        for tool in asyncio.run(server.list_tools())
    ]
    return {
        "tasks": tasks,
        "instructions": server.instructions or "",
        "tools": tools,
        "task_docs": {name: (task_doc(name) or "") for name in tasks},
    }


def build_findings(report):
    """把「AI 视角下有什么问题」挑出来。"""
    findings = []
    instructions = report["instructions"]
    if not instructions.strip():
        findings.append("instructions 为空：setting.py 四个字段都没写，AI 打开这个服务看不到任何模块定位")
    elif "调用方式：" not in instructions[:VISIBLE_WINDOW]:
        findings.append(
            f"「调用方式」不在 instructions 前 {VISIBLE_WINDOW} 字符内："
            "project_summary/description 过长，把决定调用行为的规则挤出了客户端可见窗口"
        )

    if not report["tools"]:
        findings.append(
            "一个工具都没注册：确认 LOADED_TASKS 有任务，且 API_CREATE/API_CHECK/API_RUN 至少开一个"
        )
    for name in report["tasks"]:
        doc = report["task_docs"].get(name, "").strip()
        if not doc:
            findings.append(
                f"{name}：没有模块 docstring，工具描述只剩「创建 {name} 异步任务（默认入口）」"
                "这类框架话术，AI 不知道它做什么、什么时候该用"
            )
    return findings


def render(report, env_source, findings):
    lines = [f"环境来源：{env_source}", f"任务数：{len(report['tasks'])}　工具数：{len(report['tools'])}", ""]
    lines.append(f"== instructions 前 {VISIBLE_WINDOW} 字符（客户端普遍只看这么多） ==")
    lines.append(report["instructions"][:VISIBLE_WINDOW] or "（空）")
    lines.append("")
    lines.append("== instructions 全文 ==")
    lines.append(report["instructions"] or "（空）")
    lines.append("")
    lines.append("== 工具（AI 实际看到的名称与描述） ==")
    for tool in report["tools"]:
        lines.append(f"--- {tool['name']}")
        lines.append(tool["description"] or "（无描述）")
    lines.append("")
    if findings:
        lines.append(f"== 提醒（{len(findings)}） ==")
        lines += [f"  - {item}" for item in findings]
    else:
        lines.append("== 提醒：无，描述看起来齐全 ==")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="离线预览 FastTask 的 MCP instructions 与工具描述")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument(
        "--project-dir",
        default=os.getcwd(),
        help="项目目录（放 setting.py 与 tasks/），默认当前目录",
    )
    parser.add_argument(
        "--platform-dir",
        default=None,
        help="平台代码目录（放 utils/ 与 celery_app.py），默认与项目目录相同（容器里的情形）",
    )
    args = parser.parse_args()

    project_dir = os.path.abspath(args.project_dir)
    platform_dir = os.path.abspath(args.platform_dir or args.project_dir)
    task_dir = os.path.join(project_dir, "tasks")
    if not os.path.isdir(task_dir):
        print(f"找不到任务目录 {task_dir}：用 --project-dir 指定项目根目录", file=sys.stderr)
        return 2

    env_source = prepare_env(task_dir)
    try:
        report = collect(project_dir, platform_dir)
    except Exception as error:  # noqa: BLE001 - 依赖缺失/导入失败要给出可读提示
        print(f"构建 MCP 描述失败：{type(error).__name__}: {error}", file=sys.stderr)
        print("提示：请在项目容器里跑（依赖与路径都在镜像里）：", file=sys.stderr)
        print("  podman cp <skill>/scripts/preview_mcp_descriptions.py <容器>:/tmp/", file=sys.stderr)
        print("  podman exec -w /fasttask <容器> python /tmp/preview_mcp_descriptions.py", file=sys.stderr)
        return 2

    findings = build_findings(report)
    if args.json:
        print(json.dumps({**report, "env_source": env_source, "findings": findings}, ensure_ascii=False, indent=2))
    else:
        print(render(report, env_source, findings))
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
