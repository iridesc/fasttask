## Why

`readme.md` 是项目的门面文档，新用户的第一印象和快速上手指南。经过与代码库的逐项对照审查，发现文档中存在 5 项事实性错误、4 项关键遗漏和 6 项质量问题——包括构建命令不完整、环境变量默认值与代码不一致、核心配置表缺失关键变量等问题。这些错误会导致用户按文档操作时遇到障碍，损害项目的可信度。现在修复，趁 `file-expiration-cleanup` 变更刚归档、文档内容尚热，一次性对齐。

## What Changes

- 修正构建运行命令，补充正确的 compose 文件路径
- 重写分布式部署章节的 compose 示例，对齐 `samples/docker-compose-distributed.yml` 实际内容
- 补全核心配置表：新增 `NODE_TYPE`、`MASTER_HOST`、`TASK_QUEUE_PORT`、`TASK_QUEUE_PASSWD`、`API_DOCS`、`API_STATUS_INFO`、`API_FILE_DOWNLOAD`、`API_FILE_UPLOAD`、`API_REVOKE`、`API_RUN`、`API_CREATE`、`API_CHECK`、`UVICORN_WORKERS`、`FLOWER_PORT` 等关键环境变量
- 修正 `FLOWER_MAX_TASKS` 默认值：5000 → 1000（对齐 run.py 实际值）
- 完善 `status_info` 接口说明，补充 `task_info_{task_name}` 等实际返回字段
- 补充 `FILE_EXPIRATION_SECONDS` 校验失败时的行为说明（系统报错退出）
- 在"开始使用"章节增加对 `samples/` 目录的引用
- 修正更新日志中 Redis timeout 的错误描述
- 修复标题层级（首行加 `#`）
- 统一中英文空格、代码块格式
- 展开 `API_x` 为具体的接口开关列表
- 修正文件上传下载章节重复的 UUID 示例

## Capabilities

### New Capabilities

（无。本次为纯文档修正，不引入新功能。）

### Modified Capabilities

（无。不改动任何 spec 级别的行为需求。）

## Impact

- 仅影响 `readme.md` 单一文件
- 无代码变更、无 API 变更、无依赖变更
- 无需更新测试
