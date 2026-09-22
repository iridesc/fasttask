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
- 返回后框架会用 `Result` 严格校验，校验失败任务直接失败（fail-fast）

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
├── compose.yaml            # single_node 部署（compose 文件名统一用 compose.yaml）
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
# 在项目平级目录执行，同时扫新老两种命名
grep -rhE '9[0-9][0-9][0-9]:443' ../*/compose*.y*ml ../*/docker-compose*.y*ml 2>/dev/null | sort
```

端口为示意，具体以实测 grep 结果为准，选择下一个连续空位。

compose 文件名统一用 `compose.yaml`（`podman-compose` / `docker compose` 都能自动识别）。注意
`python -m fasttask_manager.create_project` 脚手架生成的仍是 `docker-compose.yml`，照抄后改名即可。

#### .gitignore

```gitignore
tool-bin/       # 工具的二进制及依赖目录
files/          # fasttask 运行时数据
__pycache__/
*.pyc
.DS_Store
```

### 阶段 3：移植 exec_cmd.py

从既有 fasttask 项目的 `tasks/packages/exec_cmd.py` 复制到本项目同路径。以
`ez-kit/tasks/packages/exec_cmd.py` 为基准——它是最新、能力最全的一版；其它项目里还留着更早的
`check_output` 简版（没有 `cwd` / `timeout` / 进程组清理），复制时留心别拿到旧版。

核心要点：
- `subprocess.Popen` + `communicate(timeout=...)` — 取回 stdout，同时支持超时
- `preexec_fn=_set_pdeathsig`（`prctl(PR_SET_PDEATHSIG, SIGKILL)`）+ `os.setsid()` —
  给子进程建独立进程组；worker 被杀时子进程自动消失，不留孤儿
- 超时/异常时 `os.killpg` 先 `SIGTERM` 再 `SIGKILL` 整个进程组 — CLI 工具常有子进程，
  只杀父进程会留下僵尸占着端口或文件
- `stderr=subprocess.STDOUT` — stderr 合并进 stdout，失败原因不会丢
- 参数 `cwd=None, timeout=None, raise_error=True`，按需组合
- **默认 `raise_error=True` 直接抛异常** — 符合 fasttask fail-fast 哲学，失败带完整 traceback
  回到调用方；只有「工具非 0 退出但属于正常业务分支」时才传 `raise_error=False`

### 阶段 4：编写任务模块

这是核心步骤，需要仔细设计。

#### 4.1 基本结构

```python
"""一句话说明这个任务做什么、典型用途是什么。

模块 docstring 会进 MCP：首段（遇到空行即停）出现在 instructions 的任务一览，
全文作为本任务的工具说明展示给 AI 客户端，直接影响模型选工具、填参数的准确率，
所以写法建议固定在「首段一句话 + 空行 + 详细说明」（见「任务结果与 AI 接入」）。
"""

import os
from pydantic import BaseModel
from celery import current_task
# 两种导入风格都可用：框架已把 tasks/ 加进 sys.path，仓库里两种都在跑
# （新项目更倾向显式写全路径 from tasks.packages.exec_cmd import exec_cmd）
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

### 阶段 6：编写 compose.yaml

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

      # 客户端访问本服务的地址：一处决定自签证书的 CN/SAN 与外置结果的下载地址前缀。
      # 客户端用 IP/域名访问时必须配，否则证书 CN 是 localhost（Node 系客户端报
      # ERR_TLS_CERT_ALTNAME_INVALID）。写成 host 或 host:port，不要加引号。
      - PUBLIC_ENDPOINT=${PUBLIC_ENDPOINT:-127.0.0.1:9018}

      # API 设置（生产环境通常只保留 create/check）
      - API_DOCS=False
      - API_RUN=False
      - API_FILE_DOWNLOAD=False
      - API_FILE_UPLOAD=False
      # API_MCP / API_REVOKE / API_STATUS_INFO 默认 True。上层系统若靠 /revoke
      # 回收超时任务，别顺手关掉 API_REVOKE（关掉后连 MCP 的 fasttask_revoke 也没了）

      # 结果外置（可选）：AUTO = 超过 1MB 的结果自动存进内置对象存储，只回一个签名
      # 地址让调用方自行下载，避免大结果塞进 Redis / 模型上下文。不设置则结果内联
      # （与历史行为一致）。注意：开启后 /check 与 /run 的 result 都会变成引用，
      # 客户端需 fasttask_manager >= 0.6.0；分布式部署时 master 与所有 worker 必须一致
      # - RESULT_TYPE=AUTO
```

**关于 `API_RUN=False`**：禁用同步 `/run/` 端点（MCP 的 `run_<task>` 也会一起消失），只保留异步
`/create/` + `/check/`。生产环境建议关掉——同步接口会一直占着调用方连接，长任务必然失败。

**关于 `PUBLIC_ENDPOINT`**：写成 `host` 或 `host:port`。写错（典型是 compose list 形式里写成
`KEY="1.2.3.4:9018"`，引号进了值）会在**启动时直接报错**，这是刻意设计——静默生成一张坏证书，
只有等客户端连不上才暴露。证书首次启动生成到 `files/fasttask/ssl_cert/`，改值会自动重建。

**关于 `RESULT_TYPE`**：对象存储是镜像内置的（versitygw），不需要额外部署、不需要暴露端口、
也没有任何连接配置——一个开关即可。下载走 API 端口的路径代理，所以**对外**只需映射 API 端口；
分布式部署时 worker 需要能连上 master 的队列端口与对象存储端口（默认 6379 / 9000）。

### 阶段 7：构建、部署、测试

```bash
# 1. 将工具的二进制及依赖复制到项目目录
cp -r /path/to/original/tool-bin ./tool-bin

# 2. 构建镜像
podman build -t <registry>/<namespace>/<project_name>:latest .   # 按内部镜像仓库实际地址填写

# 3. 启动服务（compose.yaml 会被自动识别；PUBLIC_ENDPOINT 按实际访问地址传进来）
export PUBLIC_ENDPOINT=${PUBLIC_ENDPOINT:-127.0.0.1:9018}
podman-compose down 2>/dev/null
podman-compose up -d
# 启动日志里确认证书生成结果：SSL certificates generated ... (CN=..., SAN=...)

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
# 成功（内联）: {"state": "SUCCESS", "result_type": "json", "result": {...}}
# 成功（外置）: {"state": "SUCCESS", "result_type": "s3", "result": {"url": "...", ...}}
#              → 直接下 result.url（开头是 http 就直接用，是 / 就拼服务地址）
# 失败:         {"state": "FAILURE", "result_type": "text", "result": "Traceback..."}
```

需要 AI 客户端接入时，用官方 MCP 客户端验证（比手搓协议请求可靠）：

```bash
claude mcp add --transport http <name> https://<host>:PORT/mcp \
  --header "Authorization: Basic $(echo -n 'admin:your_password' | base64)"
```

## 任务结果与 AI 接入

### 结果外置（大结果）

FastTask 默认把结果内联在 Celery backend 里（`RESULT_TYPE=JSON`，与历史行为一致）。
结果很大时（例如扫描产物几十 MB）可以开启外置：

- `RESULT_TYPE=AUTO`：序列化后超过 `RESULT_AUTO_TO_S3_SIZE`（默认 1MB）才外置
- `RESULT_TYPE=S3`：一律外置
- 对象存储**模块内置**（versitygw），无需额外部署、无需暴露额外端口、无需配置连接信息
  （凭据由 `TASK_QUEUE_PASSWD` 派生，master 与 worker 自动一致）

开启后 `/check`、`/run` 以及 MCP 的 `check_*` / `run_*` 响应形态都会变：

```jsonc
// result_type=json（默认，内联）：result 就是任务结果
{"id": "...", "state": "SUCCESS", "result_type": "json", "result": {"items": [...]}}

// result_type=s3（外置，只回四字段引用）
{"id": "...", "state": "SUCCESS", "result_type": "s3",
 "result": {"size_bytes": 14680064, "sha256": "...",
            "url": "https://192.0.2.10:9018/fasttask-results/20260918/xxx.json?X-Amz-...",
            "expires_at": "2026-09-25T11:48:00Z"}}
```

- `result.url` 是**带签名的下载地址**：配了 `PUBLIC_ENDPOINT` 时是完整 URL（可直接下载），
  否则退回相对路径（`/fasttask-results/...`，需拼服务地址）。判断方式就是看开头是不是 `http`
- 引用里**没有** `uri` / `hint` 之类的字段；下载时**不要带 Basic 凭据**（会破坏签名）
- 预签名失败（对象存储不可用/凭据错）时整个响应降级为 `result_type=text` 并带原始报错，
  不会返回「成功但下不了」的半成品引用——稍后重试即可
- **`/run`（含 MCP `run_<task>`）同样遵循这套规则**：小结果内联，超阈值就外置。
  早期版本只对异步流程外置，且对大结果做截断，现在截断已移除（截断会造出「任务成功但数据
  被切掉」的坏状态）
- 引用有效期取 `min(RESULT_EXPIRES, 7 天)`；`FILE_EXPIRATION_SECONDS`（对象与本地文件的保留期）
  必须大于 `RESULT_EXPIRES`，否则启动报错——这条约束就是为了避免「下载地址还有效、对象已被清理」

**这是破坏性变更**：旧客户端会把引用当成结果用（而且不会报错）。因此升级必须按顺序：

1. 先把客户端升到 `fasttask_manager >= 0.6.0`（会自动下载外置结果，对调用方透明，
   同时兼容仍返回内联结果的服务端）
2. 再开启服务端的 `RESULT_TYPE=S3` 或 `AUTO`

未开启外置时任务代码不需要关心这些——返回 `result.model_dump()` 即可。

另外：`RESPONSE_COMPRESS=True`（默认）会对普通响应与结果下载做 gzip 传输压缩。客户端声明
`Accept-Encoding: gzip` 才生效，`requests` / `httpx` / 浏览器自动解压；用 curl 手动抓取时
加 `--compressed`，否则拿到的是压缩字节（`size_bytes` / `sha256` 始终按未压缩内容计算）。

### MCP 端点

镜像默认启用 MCP（`API_MCP=True`），端点为 `https://<host>:<port>/mcp`，
AI 客户端可直接调用，不需要为此改任何任务代码：

| 工具 | 说明 | 启用条件 |
|---|---|---|
| `create_<task>` | 工具参数直接来自任务的 `Params` 模型 | `API_CREATE` |
| `check_<task>` | 返回状态与结果引用（不外传大结果内容） | `API_CHECK` |
| `run_<task>` | 同步执行（仅适合单个/极少量目标、结果体量小） | `API_RUN` |
| `fasttask_status` / `fasttask_revoke` | 全局状态查询与撤销 | `API_STATUS_INFO` / `API_REVOKE` |

工具按 `API_*` 开关注册：关掉 `API_RUN` 后 `run_<task>` 就不存在，所以工具说明里写的
「优先用 create」一定成立（描述随开关动态调整）。

所以封装任务时有三件事值得做：

1. **写模块 docstring** —— 它分两层使用：首段（遇到空行即停）进 `instructions` 的任务一览，
   全文进本任务的工具说明。所以推荐写成「首段一句话 + 空行 + 详细说明」
2. **给 `Params` 字段加 `Field(description=...)`** —— 它会成为参数说明，AI 靠它决定怎么填
3. **`setting.py` 的 `project_summary` 压成一句话** —— 它排在最前面，而客户端普遍只展示
   `instructions` 的前几百字符（pi-mcp-adapter 的阈值是 300），写长了会把「怎么调用」的规则
   挤出可见窗口（超过 100 字符启动时会告警）

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

### 客户端 TLS 报证书错误（ERR_TLS_CERT_ALTNAME_INVALID / self-signed certificate）
证书是自签的，客户端需要信任（Node 系设 `NODE_EXTRA_CA_CERTS=files/fasttask/ssl_cert/cert.pem`）；
同时若客户端用 IP/域名访问，必须把 `PUBLIC_ENDPOINT` 设成该地址，否则 CN 还是 `localhost`。

### 结果下载不了 / 拿到一坨字节
- `result.url` 开头是 `/` → 是相对路径，得拼上服务地址（配 `PUBLIC_ENDPOINT` 后就不会出现）
- 下载返回 `403` → 地址过期了，重新 `check` 一次拿新地址；或用了带 Basic 凭据的客户端的
  Authorization 头破坏了签名
- curl 出来的文件打开是乱码 → 服务端启了 gzip，加 `--compressed`

### 启动即失败：`PUBLIC_ENDPOINT 含非法字符`
compose 的 list 形式里写成 `- PUBLIC_ENDPOINT="1.2.3.4:9018"`，引号进了值。去掉引号，
或改用 map 形式（`PUBLIC_ENDPOINT: 1.2.3.4:9018`）。这是刻意 fail-fast，不做容错清洗。

## 完成后

1. 更新项目的 AGENTS.md，记录项目概述和 API 调用示例
2. 提交代码到代码仓库（如 GitLab/GitHub）
3. 镜像 tag 推送到内部镜像仓库（地址按实际环境填写）
