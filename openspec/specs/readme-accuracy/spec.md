# readme-accuracy

## Purpose

确保 `readme.md` 文档内容与代码库实际行为一致，涵盖构建命令、环境变量配置表、默认值、校验行为、分布式部署示例等方面的准确性要求。

## Requirements

### Requirement: 构建运行命令应当引用实际 compose 文件

readme 中的构建与运行命令应当指向 `samples/` 目录下实际存在的 compose 文件，使用户能够直接复制执行。

#### Scenario: 单节点构建运行命令
- **WHEN** 用户按照"开始使用"章节的构建运行步骤操作
- **THEN** 命令 `docker build -t 'test_project:latest' . && docker compose -f samples/docker-compose-single_node.yml up -d` 能够成功启动服务

### Requirement: 核心配置表应当覆盖所有面向用户的环境变量

readme 的核心配置章节应当列出用户在部署时需要了解的全部环境变量，按功能分组。

涵盖的变量必须包括：`NODE_TYPE`、`MASTER_HOST`、`TASK_QUEUE_PORT`、`TASK_QUEUE_PASSWD`、`SOFT_TIME_LIMIT`、`TIME_LIMIT`、`VISIBILITY_TIMEOUT`、`RESULT_EXPIRES`、`WORKER_CONCURRENCY`、`WORKER_POOL`、`WORKER_TAG`、`ENABLED_TASKS`、`DISABLED_TASKS`、`API_RUN`、`API_CREATE`、`API_CHECK`、`API_REVOKE`、`API_FILE_DOWNLOAD`、`API_FILE_UPLOAD`、`API_STATUS_INFO`、`API_DOCS`、`UVICORN_WORKERS`、`FLOWER_ENABLED`、`FLOWER_PORT`、`FLOWER_MAX_TASKS`、`FILE_CLEANUP_ENABLED`、`FILE_EXPIRATION_SECONDS`、`DEBUG`。

#### Scenario: 用户查找部署相关配置
- **WHEN** 用户需要配置分布式部署
- **THEN** 在核心配置章节能够找到 `NODE_TYPE`、`MASTER_HOST`、`TASK_QUEUE_PORT`、`TASK_QUEUE_PASSWD` 的说明

#### Scenario: 用户查找接口开关配置
- **WHEN** 用户需要禁用某个 API 接口
- **THEN** 在核心配置章节能够找到具体的 `API_RUN`、`API_CREATE` 等开关说明，而非笼统的 `API_x`

### Requirement: 环境变量默认值应当与代码一致

readme 中声明的环境变量默认值必须与 `run.py` 中 `Env` 类定义的实际默认值一致。

#### Scenario: FLOWER_MAX_TASKS 默认值
- **WHEN** 用户查看 `FLOWER_MAX_TASKS` 配置说明
- **THEN** 文档中显示的默认值为 1000（与 run.py 中 `Env("FLOWER_MAX_TASKS", "1000")` 一致）

#### Scenario: FILE_EXPIRATION_SECONDS 默认值
- **WHEN** 用户查看 `FILE_EXPIRATION_SECONDS` 配置说明
- **THEN** 文档中显示的默认值为 `SOFT_TIME_LIMIT × 10`

### Requirement: 文件过期清理校验行为应当明确说明

readme 中 `FILE_EXPIRATION_SECONDS` 的说明应当包含校验失败时的行为：当 `FILE_CLEANUP_ENABLED=True` 且 `FILE_EXPIRATION_SECONDS < 60` 时，系统启动报错退出。

#### Scenario: 用户设置过小的过期时间
- **WHEN** 用户将 `FILE_EXPIRATION_SECONDS` 设置为小于 60 的值
- **THEN** 文档告知系统将报错退出而非静默忽略

### Requirement: 分布式部署示例应当完整可用

readme 分布式部署章节的 compose 配置示例应当包含 `FLOWER_ENABLED`、`WORKER_TAG`、`ENABLED_TASKS`/`DISABLED_TASKS` 等生产环境常用变量，并与 `samples/docker-compose-distributed.yml` 保持一致。

#### Scenario: 用户参照示例配置分布式 Worker
- **WHEN** 用户复制 readme 中的分布式部署示例
- **THEN** 示例包含 `FLOWER_ENABLED`、`WORKER_TAG`、`ENABLED_TASKS` 等配置项
