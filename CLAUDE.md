# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

FastTask 是一个基于 Python 的分布式任务平台。开发者只需定义函数 + Pydantic 输入输出模型，打包成 Docker 镜像后即可部署为带权限控制的 HTTPS 异步接口。核心技术栈：Celery（任务队列）+ Redis（broker/backend）+ FastAPI（HTTP API）+ Uvicorn（ASGI server）+ Supervisor（进程管理）。

## 构建与运行

```bash
# 构建镜像
docker build -t 'test_project:latest' .

# 单节点运行
docker compose -f samples/docker-compose-single_node.yml up -d

# 分布式运行
docker compose -f samples/docker-compose-distributed.yml up -d

# 访问 API 文档
# https://localhost:9001/docs （自签名证书，浏览器需跳过警告）
```

## 测试

```bash
# 安装测试依赖
pip install fasttask_manager requests

# 运行 Flower 功能测试（需要先启动服务）
python test/test_flower.py

# 运行单任务测试
python test/test_1.py

# 全功能集成测试（自动启停 docker compose，需要 podman-compose）
python test/full_test.py
```

测试使用 `fasttask_manager` 库作为客户端，它封装了对 create/check/run/revoke/upload/download 等接口的调用。测试默认连接 `127.0.0.1:9001`，HTTPS 协议。

## 核心架构

### 启动流程

1. `Dockerfile` 入口 `python run.py`
2. `run.py` 根据 `NODE_TYPE` 环境变量初始化不同配置（`env_type_to_envs` 字典）
3. 最终通过 `os.execv` 启动 Supervisor，由 Supervisor 管理子进程

### 三种部署模式（NODE_TYPE）

| 模式 | 启动的进程 | 用途 |
|------|-----------|------|
| `single_node` | redis + uvicorn + celery worker + flower(可选) | 单机部署，自包含 |
| `distributed_master` | redis + uvicorn + flower(可选) | 分布式 Master，提供 API + 任务队列，不执行任务 |
| `distributed_worker` | celery worker | 分布式 Worker，只执行任务，连接 Master 的 Redis |

Supervisor 配置文件：`supervisord_{NODE_TYPE}.conf`。

### 任务自动注册机制

这是理解项目的关键：

1. `utils/tools.py::load_tasks()` 扫描 `tasks/` 目录下的 `.py` 文件
2. 根据 `ENABLED_TASKS` / `DISABLED_TASKS` 过滤
3. 为每个任务在 `loaded_tasks/` 目录生成包装文件，模板在 `task_file_template` 中
4. 生成的包装文件将原始函数装饰为 Celery task（`@app.task`），注入超时、并发控制等逻辑
5. `api.py` 读取 `LOADED_TASKS` 环境变量（由 load_tasks 设置），为每个任务自动创建 `/run/{task}`、`/create/{task}`、`/check/{task}` 三个端点

### 文件结构

```
fasttask/
├── run.py              # 入口：环境变量初始化 + 启动 Supervisor
├── celery_app.py        # Celery 应用配置（Redis broker/backend、队列路由、序列化等）
├── api.py               # FastAPI 应用：生命周期、中间件、通用接口、动态生成任务接口
├── setting.py           # 项目元数据（标题、描述、版本）
├── start_flower.sh      # Flower 启动脚本（由 Supervisor 调用）
├── requirements.txt     # Python 依赖
├── supervisord_*.conf   # 三种部署模式的 Supervisor 配置
├── tasks/               # 用户编写的任务文件（业务逻辑）
│   ├── packages/        # 任务可引用的工具包
│   └── *.py             # 一个 .py 文件 = 一个任务（文件名 = 函数名）
├── utils/
│   ├── tools.py         # 任务加载、环境变量读取辅助
│   ├── api_utils.py     # 认证、Worker 状态、文件管理、Flower 代理中间件、日志中间件
│   └── redis_lock.py    # Redis 并发控制（严格锁，冲突直接失败）
└── loaded_tasks/        # 运行时生成（由 load_tasks 创建，gitignore）
```

### API 接口

**通用接口**（由 `api.py` 直接注册）：
- `POST /status_info` — Worker 状态、任务统计、待处理任务数
- `GET /download` — 文件下载（有路径遍历防护）
- `POST /upload` — 文件上传
- `POST /revoke` — 撤销任务

**任务接口**（为每个 LOADED_TASKS 中的任务动态生成）：
- `POST /run/{task_name}` — 同步执行任务
- `POST /create/{task_name}` — 创建异步任务（返回 result_id）
- `GET /check/{task_name}?result_id=xxx` — 查询任务结果

**Flower 代理**（当 `FLOWER_ENABLED=True` 时）：
- `/flower/*` — 通过 `FlowerProxyMiddleware` 代理到内部 Flower 服务

### 并发控制

任务支持基于 Redis 的并发控制。调用时通过 `fasttask_concurrency_params` 参数指定：
- `concurrency_key`：并发标识（相同 key 共享限流）
- `max_concurrency`：最大并发数（默认 16）
- `countdown`：获取锁失败后的重试等待秒数（默认 60）
- `expire`：锁过期时间，防止死锁（默认 1800 秒）

并发控制逻辑在 `utils/tools.py::task_file_template` 中生成，使用 `utils/redis_lock.py::RedisConcurrencyController`，锁 key 前缀为 `fasttask:lock:{task_name}:`。

### 认证

当 `files/fasttask/conf/user_to_passwd.json` 存在且非空时，自动启用 HTTP Basic 认证；否则所有请求以 `Anonymous` 身份通过。认证通过 `get_current_username` 依赖注入实现，Flower 路径在中间件层面独立处理认证。

### 关键环境变量

- `NODE_TYPE`：部署模式，必填（`single_node` / `distributed_master` / `distributed_worker`）
- `SOFT_TIME_LIMIT`：任务软超时（秒），默认 86400，超时后发送 SIGKILL
- `TIME_LIMIT`：硬超时，默认 `SOFT_TIME_LIMIT + 60`
- `VISIBILITY_TIMEOUT`：Celery broker 可见性超时，默认 `TIME_LIMIT + 60`
- `RESULT_EXPIRES`：结果过期时间（秒），默认 259200（3 天）
- `WORKER_CONCURRENCY`：Worker 并发数，默认 CPU 核数
- `WORKER_POOL`：Worker 池类型，默认 `prefork`，可选 `gevent`
- `ENABLED_TASKS` / `DISABLED_TASKS`：控制 Worker 执行的任务白名单/黑名单
- `FLOWER_ENABLED`：是否启用 Flower 监控，默认 `False`
- `API_RUN` / `API_CREATE` / `API_CHECK` 等：控制各类接口是否启用，默认 `True`
- `DEBUG`：启用后通过 `LoggingMiddleware` 打印详细请求/响应日志

### Redis 数据库分布

- `db=0`：RUNNING_ID、并发锁
- `db=1`：Celery broker（任务队列）
- `db=2`：Celery backend（任务结果）

### 任务编写规范

1. 文件名与函数名必须一致
2. 必须定义 `Params(BaseModel)` 和 `Result(BaseModel)`，属性名与函数参数名一致
3. 结果需以 `result.model_dump()` 或字典形式返回
4. 异常会被捕获并返回完整 traceback
5. 可通过 `tasks/packages/` 下的模块共享工具函数
