## Context

`readme.md` 是项目根目录下的主文档，面向新用户介绍 FastTask 的安装、使用和配置。经过逐项对照 `fasttask/run.py`、`fasttask/api.py`、`fasttask/celery_app.py`、`samples/docker-compose-*.yml` 等实际代码文件审查，共发现 15 项问题。

当前 readme 约 285 行，涵盖简介、特性、开始使用、分布式部署、并发控制、Flower 监控、认证、文件管理、核心配置、更新日志等章节。

## Goals / Non-Goals

**Goals:**
- 修正所有与代码实际行为不一致的事实性错误（构建命令、默认值、环境变量列表等）
- 补全遗漏的关键信息（环境变量、接口返回字段等）
- 统一文档格式质量（标题层级、中英文空格、代码块格式）
- 确保新用户按文档操作每一步都能成功

**Non-Goals:**
- 不修改任何 Python 代码、配置文件或 Dockerfile
- 不新增章节或重构文档结构
- 不添加截图、GIF 等多媒体内容
- 不翻译为英文

## Decisions

### 1. 分布式部署示例：对齐 samples/docker-compose-distributed.yml

readme 当前使用手写的简化 docker compose 片段，与实际 `samples/docker-compose-distributed.yml` 差距较大。

**决定**：将示例改为引用实际 compose 文件的关键片段，补充 `FLOWER_ENABLED`、`WORKER_TAG`、`ENABLED_TASKS`/`DISABLED_TASKS` 等环境变量。同时增加指向 `samples/` 目录的说明。

**理由**：用户拷贝 readme 中的片段后无法直接使用，必须对照实际文件。直接引用实际配置可减少维护负担。

### 2. 核心配置表：展开为完整的三级分类

当前配置表有 16 个条目，但遗漏了约 14 个关键变量。

**决定**：将配置表按功能分类重组为三级：
- **部署与网络**：`NODE_TYPE`、`MASTER_HOST`、`TASK_QUEUE_PORT`、`TASK_QUEUE_PASSWD`、`UVICORN_WORKERS`
- **任务执行**：`SOFT_TIME_LIMIT`、`TIME_LIMIT`、`VISIBILITY_TIMEOUT`、`RESULT_EXPIRES`、`WORKER_CONCURRENCY`、`WORKER_POOL`、`WORKER_TAG`、`ENABLED_TASKS`、`DISABLED_TASKS`
- **接口控制**：`API_RUN`、`API_CREATE`、`API_CHECK`、`API_REVOKE`、`API_FILE_DOWNLOAD`、`API_FILE_UPLOAD`、`API_STATUS_INFO`、`API_DOCS`
- **Flower 监控**：`FLOWER_ENABLED`、`FLOWER_PORT`、`FLOWER_MAX_TASKS`
- **文件清理**：`FILE_CLEANUP_ENABLED`、`FILE_EXPIRATION_SECONDS`
- **调试**：`DEBUG`

**理由**：用户按功能查找配置远比按字母顺序高效。同时避免 `API_x` 这种模糊的表述。

### 3. FLOWER_MAX_TASKS 默认值：修正为 1000

readme 写 5000，但 `run.py` 中两处均设置为 `Env("FLOWER_MAX_TASKS", "1000")`。`start_flower.sh` 的 shell 默认 `${FLOWER_MAX_TASKS:-3000}` 由于 Python 层已设置环境变量而永远不会触发。

**决定**：文档写 1000，与 Python 代码保持一致。

**理由**：Python `Env` 类是事实上的配置源，Shell 的默认值只是冗余兜底。

### 4. 更新日志中 Redis timeout 描述：对齐当前代码

更新日志 2025-05 条目写 "timeout 改为 0（不主动断开）"，但当前 `celery_app.py` 中 `socket_timeout: 60`。

**决定**：将描述改为 "socket_timeout 设为 60 秒，增加 socket_keepalive、retry_on_timeout"。

**理由**：代码是唯一的事实来源。可能历史上确实是 0，后来改为 60，但文档需要反映当前状态。

### 5. 标题层级：首行加 `#`

首行 "FastTask 分布式任务平台" 缺少 `#`，导致它被渲染为普通段落而非标题。

**决定**：改为 `# FastTask 分布式任务平台`。

## Risks / Trade-offs

- **风险**：修改后可能引入新的笔误 → **缓解**：逐项对照代码复核每个数字和变量名
- **风险**：配置分类方式可能有人不认同 → **缓解**：分类基于功能边界（run.py 中 `env_type_to_envs` 的分组逻辑），而非主观偏好
