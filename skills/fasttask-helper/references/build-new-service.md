# 功能组 1：封装构建新服务

> fasttask-helper 技能「封装构建新服务」功能组的完整操作手册。
> 用途：将任意 CLI 工具封装为 FastTask HTTPS API 异步服务（项目骨架、任务模块、Docker、部署、认证）。
> 触发场景：用户说"用 fasttask 封装 XX"、"把 XX 命令封装为 API"、"为 XX 创建 fasttask 项目"、
> "把 XX 工具做成服务"、或需要将现有二进制工具包装为异步任务+查询接口。

将一个 CLI 工具封装为 FastTask 异步任务服务的标准化流程。

## 前置知识

FastTask 是一个分布式任务平台（Celery + Redis + FastAPI + Uvicorn）。
开发者只需定义函数 + Pydantic 输入输出模型，打包成 Docker 镜像后即可部署为带权限控制的 HTTPS 异步接口。

**一个任务模块 = 一个 .py 文件**，其中：
- 文件名 = 函数名
- `Params(BaseModel)` 定义输入，属性名与函数参数名一致
- `Result(BaseModel)` 定义输出
- 函数返回 `result.model_dump()`
- **不要 try/except**，异常直接抛出，由 fasttask 框架记录完整 traceback

项目形态（既有部署可作参考，注意随项目分发、不写死本机路径）：
- 外部二进制封装型 — subprocess 调用外部 CLI 的典型
- 多任务型 — 一个服务内含多个任务模块
- 纯逻辑型 — 无外部二进制，纯 Python 处理
- 重型依赖型 — 需要 Chromium 等浏览器/大依赖

## 封装流程（7 个阶段）

### 阶段 1：理解 CLI 工具

动手之前，先把工具吃透：

1. **运行 `--help`** 了解子命令、参数、选项、默认值
2. **列出依赖文件**：配置文件（如 config.yaml）、许可证文件、数据库文件、字典文件等
3. **确认输出格式**：文本？JSON？二进制文件？输出到 stdout 还是 `-o` 指定的文件？
4. **实测一次**：在宿主机上跑一次完整命令，验证行为和理解

### 阶段 2：创建项目骨架

在项目工作目录（通常为若干 fasttask 项目平级放置的目录）下创建项目目录：

```
my_project/
├── setting.py              # FastTask 元数据
├── requirements.txt        # 额外 pip 依赖（按需，可为空）
├── Dockerfile              # 基于 fasttask 官方基础镜像
├── docker-compose.yml      # single_node 部署
├── .gitignore              # 排除二进制、运行时数据
├── files/                  # 运行时挂载卷（整个目录 gitignore）
└── tasks/
    ├── my_task.py          # 任务模块（文件名 = 函数名）
    └── packages/
        ├── __init__.py     # 可为空
        └── exec_cmd.py     # subprocess 封装（从参考项目移植）
```

**关键点**：`tasks/` 下 **不要放 `__init__.py`**，否则 fasttask 会把它当任务模块扫描导致启动失败。`tasks/packages/__init__.py` 可以有。

#### setting.py 模板

```python
# fasttask setting
project_title = "⚡项目名⚡"
project_summary = "一句话描述"
project_description = "详细描述"
project_version = "0.1.0"
```

#### 端口分配

检查已有项目的端口占用，选择下一个连续端口：

```bash
grep -rh '"9[0-9][0-9][0-9]:443' ../*/docker-compose.yml | sort  # 在项目平级目录执行
```

端口为示意，具体以实测 grep 结果为准，选择下一个连续空位。

#### .gitignore

```gitignore
tool-bin/       # 工具的二进制及依赖目录
files/          # fasttask 运行时数据
__pycache__/
*.pyc
.DS_Store
```

### 阶段 3：移植 exec_cmd.py

从任一既有 fasttask 项目的 `tasks/packages/exec_cmd.py` 复制到本项目同路径。

核心要点：
- `subprocess.check_output()` — 命令失败自动抛异常
- `preexec_fn=_set_pdeathsig` — 父进程死亡时子进程自动被 kill，不留孤儿进程
- `stderr=subprocess.STDOUT` — stderr 合并到 stdout
- 支持 `cwd` 参数 — 指定命令的工作目录
- **不捕获异常** — 符合 fasttask fail-fast 哲学

### 阶段 4：编写任务模块

这是核心步骤，需要仔细设计。

#### 4.1 基本结构

```python
"""一句话说明这个任务做什么、典型用途是什么。

模块 docstring 会作为 MCP 工具说明展示给 AI 客户端（见「任务结果与 AI 接入」），
直接影响模型选工具、填参数的准确率，值得认真写。
"""

import os
from pydantic import BaseModel
from celery import current_task
from packages.exec_cmd import exec_cmd


FILES_DIR = "/fasttask/files"


class Params(BaseModel):
    # 输入参数，属性名与函数参数名一致
    items: list[str] = []


class Result(BaseModel):
    # 输出结构
    results: dict[str, str] = {}


def my_task(items: list[str]):
    # 1. 参数校验（fail-fast）
    if len(items) == 0:
        raise ValueError("items 不能为空")

    # 2. 创建任务独立工作目录
    task_id = current_task.request.id
    task_dir = os.path.join(FILES_DIR, task_id)
    os.makedirs(task_dir, exist_ok=True)

    # 3. 遍历处理
    results = {}
    total = len(items)
    for index, item in enumerate(items):
        # 构建命令、执行、收集结果
        print(f"[{index + 1}/{total}] 处理中: {item}")
        # ... 业务逻辑 ...
        print(f"[{index + 1}/{total}] {item} 完成")

    return Result(results=results).model_dump()
```

#### 4.2 调用外部二进制的模式

工具依赖文件（配置、许可证等）放在容器内固定目录（如 `/tool-bin/`），
二进制软链接到 `/usr/local/bin/` 方便直接调用：

```python
# 构建命令
cmd = [
    "binary_name",           # 通过 PATH 软链接调用
    "-c", "/tool-bin/config.yaml",   # 绝对路径指定配置
    "--lic", "/tool-bin/license",    # 绝对路径指定许可证
    "subcommand",
    "--input", input_file,   # 输入文件（相对路径，在 task_dir 下）
    "-o", output_file,       # 输出文件（相对路径，在 task_dir 下）
]
# 在任务目录下执行
exec_cmd(cmd, cwd=task_dir)
```

#### 4.3 文件输入输出模式（推荐）

当 CLI 工具支持 `--input-file` / `-o` 时，用文件传递数据更可靠：

```python
import uuid

file_tag = str(uuid.uuid4())
input_file = f"{file_tag}_input.txt"
output_file = f"{file_tag}_output.txt"

# 写入输入文件
with open(os.path.join(task_dir, input_file), "w") as f:
    f.write(item_data)

# 执行
exec_cmd([...], cwd=task_dir)

# 读取输出文件
with open(os.path.join(task_dir, output_file), "r") as f:
    result = f.read()
```

#### 4.4 串行 vs 并行

一个任务内的多个处理对象默认**串行执行**（简单的 for 循环）。
多个任务的并发由 fasttask 的 `WORKER_CONCURRENCY` 和 `fasttask_concurrency_params` 控制。

如需在处理过程中限制某些资源的并发，用 fasttask 内置的并发控制：
```json
{
  "fasttask_concurrency_params": {
    "concurrency_key": "resource_name",
    "max_concurrency": 5
  }
}
```

### 阶段 5：编写 Dockerfile

```dockerfile
FROM <fasttask-base-image>:latest   # fasttask 官方基础镜像，如 docker.io/irid/fasttask:latest

# 创建工具目录并拷贝二进制及全部依赖
RUN mkdir -p /tool-bin
COPY tool-bin/binary /tool-bin/binary
COPY tool-bin/config.yaml /tool-bin/config.yaml
COPY tool-bin/license /tool-bin/license
# 拷贝工具引用的所有文件（字典、数据库等）
COPY tool-bin/dict.txt /tool-bin/dict.txt
RUN chmod +x /tool-bin/binary

# 软链接到 PATH，可直接用命令名调用
RUN ln -s /tool-bin/binary /usr/local/bin/binary

# 如果配置文件中有宿主机绝对路径，需要修正为容器内路径
# RUN sed -i 's|/original/path|/tool-bin/path|g' /tool-bin/config.yaml

# FastTask 常规设置
WORKDIR /fasttask
COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt
COPY setting.py setting.py
RUN rm -rf tasks
COPY tasks tasks
```

**关键**：`RUN rm -rf tasks` 删除 fasttask 自带的示例任务，然后 `COPY tasks tasks` 放入我们的任务。

### 阶段 6：编写 docker-compose.yml

```yaml
services:
  project_name:
    image: <registry>/<namespace>/<project_name>:latest   # 内部镜像仓库按实际填写
    container_name: project_name
    restart: always

    ports:
      - "9018:443"           # 顺序分配端口

    volumes:
      - ./files:/fasttask/files    # 持久化任务输出

    environment:
      - NODE_TYPE=single_node
      - WORKER_CONCURRENCY=4       # Worker 并发数，按需调整

      # API 设置（生产环境只保留 create/check）
      - API_REDOC=False
      - API_RUN=False
      - API_FILE_DOWNLOAD=False
      - API_FILE_UPLOAD=False
```

**关于 `API_RUN=False`**：禁用同步 `/run/` 端点，只保留异步 `/create/` + `/check/`。长时间任务必须走异步。

### 阶段 7：构建、部署、测试

```bash
# 1. 将工具的二进制及依赖复制到项目目录
cp -r /path/to/original/tool-bin ./tool-bin

# 2. 构建镜像
podman build -t <registry>/<namespace>/<project_name>:latest .   # 按内部镜像仓库实际地址填写

# 3. 启动服务
podman-compose down 2>/dev/null
podman-compose up -d

# 4. 配置 API 认证（fasttask 检测到该文件非空时自动启用 Basic Auth）
mkdir -p files/fasttask/conf
echo '{"admin":"your_password"}' > files/fasttask/conf/user_to_passwd.json
podman-compose down && podman-compose up -d  # 重启生效

# 5. 创建任务
curl -sk -u admin:your_password -X POST https://localhost:PORT/create/task_name \
  -H "Content-Type: application/json" \
  -d '{"param1": "value1"}'
# 返回: {"id": "xxx-xxx", "state": "PENDING", "result": ""}

# 6. 查询结果
curl -sk -u admin:your_password "https://localhost:PORT/check/task_name?result_id=xxx-xxx"
# 成功: {"state": "SUCCESS", "result": {...}}
# 失败: {"state": "FAILURE", "result": "Traceback..."}
```

## 任务结果与 AI 接入

### 结果外置（大结果）

FastTask 默认把结果内联在 Celery backend 里（`RESULT_TYPE=JSON`，与历史行为一致）。
结果很大时（例如扫描产物几十 MB）可以开启外置：

- `RESULT_TYPE=AUTO`：序列化后超过 `RESULT_AUTO_TO_S3_SIZE`（默认 1MB）才外置
- `RESULT_TYPE=S3`：一律外置
- 对象存储**模块内置**（versitygw），无需额外部署、无需暴露额外端口、无需配置连接信息；
  下载地址是相对路径，通过 API 端口的路径代理对外提供

开启后 `/check` 的响应形态会变：

```jsonc
// result_type=json（默认，内联）
{"id": "...", "state": "SUCCESS", "result_type": "json", "result": {"items": [...]}}

// result_type=s3（外置，只回引用）
{"id": "...", "state": "SUCCESS", "result_type": "s3",
 "result": {"uri": "s3://...", "url": "/fasttask-results/20260918/xxx.json?X-Amz-...",
            "size_bytes": 14680064, "sha256": "..."}}
```

**这是破坏性变更**：旧客户端会把引用当成结果用（而且不会报错）。因此升级必须按顺序：

1. 先把客户端升到 `fasttask_manager >= 0.6.0`（会自动下载外置结果，对调用方透明，
   同时兼容仍返回内联结果的服务端）
2. 再开启服务端的 `RESULT_TYPE=S3` 或 `AUTO`

另外两点：

- **同步接口 `/run` 不做外置**：它即时消费结果，直接返回（MCP 侧超过 200KB 会截断并给出提示）
- 需要完整结果时优先用 `create` + `check`（只有异步流程才外置）
- 未开启外置时任务代码不需要关心这些——返回 `result.model_dump()` 即可

### MCP 端点

镜像默认启用 MCP（`API_MCP=True`），端点为 `https://<host>:<port>/mcp`，
AI 客户端可直接调用，不需要为此改任何任务代码：

| 工具 | 说明 |
|---|---|
| `create_<task>` | 工具参数直接来自任务的 `Params` 模型 |
| `check_<task>` | 返回状态与结果引用（不外传大结果内容） |
| `run_<task>` | 同步执行（仅适合秒级完成的任务） |
| `fasttask_status` / `fasttask_revoke` | 全局状态查询与撤销 |

所以封装任务时有两件事值得做：

1. **写模块 docstring** —— 它会成为工具说明
2. **给 `Params` 字段加 `Field(description=...)`** —— 它会成为参数说明，AI 靠它决定怎么填

`ENABLED_TASKS` 可控制暴露范围（工具数量随任务数增长，每个任务最多 3 个）。

## 常见问题排查

### 工具不生成输出文件
- 检查工具依赖的**所有**文件是否都 COPY 进镜像（对照 config.yaml 中的文件路径逐一核实）
- 检查配置文件中的绝对路径是否在容器内存在（用 `sed` 修正）
- 在任务代码中加 `print(os.listdir(task_dir))` 调试，看工作目录下有哪些文件

### OSError: Invalid cross-device link
`os.rename()` 不支持跨文件系统。容器镜像层 → 挂载卷时必现。**用 `shutil.move()` 代替**。

### 工具找不到配置
- 确认 `-c` / `--config` 使用了绝对路径
- 确认 `cwd` 参数设置正确
- 部分带许可/本地库文件的 CLI 工具需要在自身目录运行以找到这些文件，设置 `cwd=/tool-bin`

### tasks/__init__.py 导致启动失败
错误信息：`module 'tasks.__init__' has no attribute 'Params'`。
原因：fasttask 的 `load_task_names()` 扫描 `tasks/*.py`，`__init__.py` 被当作任务模块。
解决：删除 `tasks/__init__.py`（`tasks/packages/__init__.py` 不受影响）。

### 认证失败 "Not authenticated"
`files/fasttask/conf/user_to_passwd.json` 不存在或为空时 fasttask 不启用认证。
确保该文件存在且内容为有效 JSON `{"user": "password"}`，并重启容器。

## 完成后

1. 更新项目的 AGENTS.md，记录项目概述和 API 调用示例
2. 提交代码到代码仓库（如 GitLab/GitHub）
3. 镜像 tag 推送到内部镜像仓库（地址按实际环境填写）
