# file-expiration

## Purpose

FastTask 文件过期清理机制。由 Supervisor 管理的独立进程周期性扫描 `files/` 目录，基于文件 mtime 删除过期文件，并清理留下的空目录。通过环境变量灵活配置过期时间和启用/禁用。

## Requirements

### Requirement: 通过环境变量配置文件过期清理

系统 SHALL 支持通过 `FILE_EXPIRATION_SECONDS` 环境变量配置文件过期时间。

当 `FILE_EXPIRATION_SECONDS` 未设置时，系统 SHALL 使用 `SOFT_TIME_LIMIT` 的 10 倍作为默认值。

文件清理使用独立开关 `FILE_CLEANUP_ENABLED`（默认 `True`），设为 `False` 时清理进程不启动。

当 `FILE_CLEANUP_ENABLED=True` 时，系统 SHALL 校验 `FILE_EXPIRATION_SECONDS >= 60`，不满足则启动报错退出。

#### Scenario: 使用默认过期时间
- **WHEN** 未设置 `FILE_EXPIRATION_SECONDS` 环境变量，且 `SOFT_TIME_LIMIT=86400`
- **THEN** 文件过期时间为 864000 秒（10 天）

#### Scenario: 自定义过期时间
- **WHEN** 设置 `FILE_EXPIRATION_SECONDS=3600`
- **THEN** 文件过期时间为 3600 秒（1 小时）

#### Scenario: 过期时间过小报错
- **WHEN** `FILE_CLEANUP_ENABLED=True`，`FILE_EXPIRATION_SECONDS=30`
- **THEN** 系统启动报错，提示 FILE_EXPIRATION_SECONDS 不得小于 60 秒

#### Scenario: 禁用清理进程
- **WHEN** 设置 `FILE_CLEANUP_ENABLED=False`
- **THEN** Supervisor 不启动 file_cleanup 进程，不执行任何文件清理，且不校验 FILE_EXPIRATION_SECONDS

### Requirement: 独立进程周期性清理过期文件

系统 SHALL 通过 Supervisor 管理的独立进程（`cleanup_files.py`）定期扫描并删除过期文件。

清理进程 SHALL 覆盖所有部署模式（single_node、distributed_master、distributed_worker）。

系统 SHALL 在进程启动时立即执行一次清理，之后每隔 600 秒（10 分钟）执行一次。

系统 SHALL 基于文件的修改时间（`mtime`）与当前时间的差值判断是否过期。

系统 SHALL NOT 对 `files/fasttask/` 目录及其子目录和文件执行过期清理。

系统 SHALL 使用 `os.walk(topdown=False)` 自底向上递归遍历所有子目录，删除过期文件后，若目录为空则一并清理空目录。

#### Scenario: 删除过期文件
- **WHEN** 文件 `files/test.dat` 的 mtime 距当前时间超过 `FILE_EXPIRATION_SECONDS` 秒
- **THEN** 该文件被删除

#### Scenario: 保留未过期文件
- **WHEN** 文件 `files/test.dat` 的 mtime 距当前时间未超过 `FILE_EXPIRATION_SECONDS` 秒
- **THEN** 该文件被保留

#### Scenario: 跳过系统子目录
- **WHEN** 文件位于 `files/fasttask/` 或其任意子目录下
- **THEN** 该文件不被检查，始终保留

#### Scenario: 清理嵌套子目录中的过期文件
- **WHEN** `files/a/b/test.dat` 的 mtime 已过期
- **THEN** 该文件被删除；若 `a/b/` 变为空目录，一并删除；若 `a/` 也变空，继续向上清理

#### Scenario: 非空目录保留
- **WHEN** 目录中仍有未过期文件
- **THEN** 该目录被保留，不删除

#### Scenario: 启动时立即清理
- **WHEN** file_cleanup 进程启动完成后
- **THEN** 立即执行一次文件过期扫描和清理

### Requirement: Supervisor 配置模板组装

系统 SHALL 将 Supervisor 配置拆分为独立模板文件（`supervisord_template_conf/`），在启动时由 `run.py` 中的 `assemble_supervisor_conf()` 根据 `NODE_TYPE`、`FLOWER_ENABLED`、`FILE_CLEANUP_ENABLED` 动态组装到 `supervisord_conf/` 目录。

所有子进程的 Supervisor 配置 SHALL 将 stdout/stderr 打到 `/dev/stdout` 和 `/dev/stderr`，方便 `docker logs` 统一查看。

Supervisor 主配置 SHALL 设置 `logfile=/dev/stdout` 和 `pidfile=/dev/null`，不写本地日志和 pid 文件。

#### Scenario: single_node 组装
- **WHEN** `NODE_TYPE=single_node`，`FLOWER_ENABLED=True`，`FILE_CLEANUP_ENABLED=True`
- **THEN** `supervisord_conf/` 包含：supervisord.conf、redis.conf、uvicorn.conf、celery.conf、flower.conf、file_cleanup.conf

#### Scenario: distributed_worker 组装
- **WHEN** `NODE_TYPE=distributed_worker`，`FILE_CLEANUP_ENABLED=True`
- **THEN** `supervisord_conf/` 包含：supervisord.conf、celery.conf、file_cleanup.conf

#### Scenario: FLOWER_ENABLED=False
- **WHEN** `FLOWER_ENABLED=False`
- **THEN** `supervisord_conf/` 中不含 flower.conf，Flower 进程不启动

#### Scenario: FILE_CLEANUP_ENABLED=False
- **WHEN** `FILE_CLEANUP_ENABLED=False`
- **THEN** `supervisord_conf/` 中不含 file_cleanup.conf，清理进程不启动
