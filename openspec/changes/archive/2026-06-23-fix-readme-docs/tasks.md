## 1. 标题与格式修正

- [x] 1.1 首行 "FastTask 分布式任务平台" 前添加 `# ` 使其成为一级标题
- [x] 1.2 统一全文中英文之间的空格（python函数 → Python 函数，https接口 → HTTPS 接口 等）
- [x] 1.3 修正代码块语言标记缩进不一致问题（第 28 行 ` ```bash` 缩进异常）

## 2. 开始使用章节修正

- [x] 2.1 修正构建运行命令：`docker compose up -d` → `docker compose -f samples/docker-compose-single_node.yml up -d`
- [x] 2.2 在"打包运行"步骤中增加说明：compose 文件位于 `samples/` 目录下，单节点用 `docker-compose-single_node.yml`，分布式用 `docker-compose-distributed.yml`
- [x] 2.3 修正"特性"第 5 项描述："基于python：slim" → "基于 `python:slim` Docker 镜像"

## 3. status_info 接口说明完善

- [x] 3.1 补充 `status_info` 接口的固定返回字段说明：`running_id`、`username`
- [x] 3.2 补充 `task_info_{task_name}` 按任务维度统计字段的说明
- [x] 3.3 修正 `/check/{task_name}` 接口描述中 `result_id` 参数说明为 query 参数（与代码一致）

## 4. 分布式部署章节修正

- [x] 4.1 将手写 docker compose 片段改为引用 `samples/docker-compose-distributed.yml` 的关键内容
- [x] 4.2 master 节点配置补充 `FLOWER_ENABLED=True`
- [x] 4.3 worker 节点配置补充 `FLOWER_ENABLED`、`WORKER_TAG`、`ENABLED_TASKS`/`DISABLED_TASKS` 示例
- [x] 4.4 修正 `MASTER_HOST=10.65.8.8` → `MASTER_HOST=master`（使用 Docker 服务名）
- [x] 4.5 补充说明：master 节点暴露 6379 端口供 worker 连接，worker 节点不需要暴露 6379

## 5. 核心配置表重构

- [x] 5.1 将配置表按功能分组：部署与网络、任务执行、接口控制、Flower 监控、文件清理、调试
- [x] 5.2 补充遗漏变量：`NODE_TYPE`、`MASTER_HOST`、`TASK_QUEUE_PORT`、`TASK_QUEUE_PASSWD`、`UVICORN_WORKERS`
- [x] 5.3 补充遗漏变量：`API_DOCS`、`API_STATUS_INFO`、`API_FILE_DOWNLOAD`、`API_FILE_UPLOAD`、`API_REVOKE`、`API_RUN`、`API_CREATE`、`API_CHECK`（替代笼统的 `API_x`）
- [x] 5.4 补充遗漏变量：`WORKER_TAG`、`FLOWER_PORT`
- [x] 5.5 修正 `FLOWER_MAX_TASKS` 默认值：5000 → 1000
- [x] 5.6 补充 `FILE_EXPIRATION_SECONDS` 校验失败说明：当 `FILE_CLEANUP_ENABLED=True` 且值 < 60 时，系统启动报错退出

## 6. 文件管理章节修正

- [x] 6.1 修正上传下载示例中相同的 UUID 文件名，使用不同的示例值以区分上传和下载场景

## 7. 更新日志修正

- [x] 7.1 修正 2025-05 条目 Redis 连接优化描述："timeout 改为 0" → "socket_timeout 设为 60 秒"
- [x] 7.2 统一更新日志日期格式（所有条目使用完整的 YYYY-MM-DD 或统一为 YYYY-MM）
