## 1. 环境变量配置

- [x] 1.1 在 `run.py` 的 `env_type_to_envs["common"]` 中添加 `FILE_EXPIRATION_SECONDS` 环境变量，默认值为 `SOFT_TIME_LIMIT` 的 10 倍（使用 lambda 延迟计算）

## 2. 核心清理逻辑

- [x] 2.1 创建 `cleanup_files.py` 独立清理脚本：使用 `os.walk` 递归遍历，跳过 `files/fasttask/`，基于 mtime 删除过期文件和空目录，每 600 秒循环

## 3. Supervisor 配置重构

- [x] 3.1 创建 `supervisord_template_conf/` 目录，将服务配置拆分为独立文件：`supervisord.conf`（主配置 + include）、`redis.conf`、`uvicorn.conf`、`celery.conf`、`flower.conf`、`file_cleanup.conf`
- [x] 3.2 在 `run.py` 中添加 `assemble_supervisor_conf()` 函数，根据 `NODE_TYPE`、`FLOWER_ENABLED`、`FILE_EXPIRATION_SECONDS` 动态组装配置到 `supervisord_conf/`
- [x] 3.3 修改 `start()` 使用动态组装的 `supervisord_conf/supervisord.conf`
- [x] 3.4 删除旧的 `supervisord_*.conf` 单体配置文件
- [x] 3.5 在 `.gitignore` 中添加 `fasttask/supervisord_conf/`

## 4. 禁用方式

- `FILE_EXPIRATION_SECONDS=0` → `assemble_supervisor_conf()` 不复制 `file_cleanup.conf`，清理服务不启动
