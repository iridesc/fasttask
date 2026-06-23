## Why

用户上传到 `files/` 目录的文件目前会永久保留，缺乏自动清理机制。随着平台持续运行，上传文件会不断累积并占用磁盘空间。需要增加一种基于文件存留时间的自动过期删除机制，确保磁盘空间得到合理回收。

## What Changes

- 新增 `FILE_EXPIRATION_SECONDS` 环境变量，控制文件过期时间，默认值为 `SOFT_TIME_LIMIT` 的 10 倍（即 10 天）
- 排除 `files/fasttask/` 子目录（系统内部文件目录），不对此目录下的文件进行过期清理
- 在 `run.py` 中添加 `FILE_EXPIRATION_SECONDS` 的环境变量初始化
- 在 `utils/api_utils.py` 中添加文件过期清理函数，基于文件修改时间（mtime）判断是否过期
- 在 `api.py` 的 lifespan 中启动后台清理任务，周期性地扫描并删除过期文件
- 对 `/download` 接口增加检查：如果请求的文件已过期，返回 404

## Capabilities

### New Capabilities
- `file-expiration`: 文件自动过期清理能力，包括过期判断、定期扫描删除、下载时的过期检查

### Modified Capabilities
<!-- 无需修改现有 spec -->

## Impact

- 受影响文件：`fasttask/run.py`、`fasttask/api.py`、`fasttask/utils/api_utils.py`
- 新增环境变量 `FILE_EXPIRATION_SECONDS`
- 不影响现有 `/upload`、`/download` 的 API 契约（过期文件下载返回 404 属于合理的错误响应）
- 不影响 `files/fasttask/` 下的系统文件（Redis、Celery、SSL 证书、配置、日志等）
