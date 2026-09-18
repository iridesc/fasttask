---
name: fasttask-helper
description: >
  FastTask 平台助手，覆盖两类工作，按场景自动选择功能组：
  (1) 封装构建新服务：将任意 CLI 工具封装为 FastTask HTTPS API 异步服务（项目骨架、任务模块、
  Docker、部署、认证）；
  (2) 管理已部署实例上的任务：查看实例/worker/任务统计、查询任务结果、撤销（revoke）running/
  卡住/误下发的任务、批量终止一组 result_id。
  触发关键词：fasttask 封装/做成 API/创建 fasttask 项目/把 XX 工具做成服务/部署新模块/
  撤销任务/revoke/任务一直 running 卡住想停掉/查任务状态/看实例上有哪些任务/status_info/
  管理某 fasttask 实例任务。即使用户只模糊提到"调一下接口看看""撤销掉这批任务"也应触发本技能。
---

# FastTask Helper

FastTask 平台（Celery + Redis + FastAPI + Uvicorn）的封装与运维助手。

## 先选功能组

本技能拆成两个独立功能组，**根据用户意图选择对应的参考文档**，不要只依赖本文件：

| 用户想要… | 功能组 | 去读 |
|---|---|---|
| 把 CLI 工具封装成 HTTPS API、新建 fasttask 项目/模块、打包部署 | ① 封装构建新服务 | [references/build-new-service.md](references/build-new-service.md) |
| 管理已运行的 fasttask 实例：查状态/结果、撤销任务、批量终止 | ② 管理存在的实例任务 | [references/manage-instances.md](references/manage-instances.md) |

两个功能组都完整独立成文，各自含操作步骤、代码模板与排查项。先读对应文档再动手。

## 公共基础知识（两功能组通用）

- **FastTask 是什么**：分布式异步任务平台。开发者定义 `tasks/*.py`（文件名=函数名，含
  `Params`/`Result` Pydantic 模型），打包 Docker 镜像部署，暴露带权限控制的 HTTPS 异步接口。
  一次调用 = 一个 celery 任务，有独立 `result_id`。
- **实例与 RUNNING_ID**：每个运行中的服务是一个实例，启动时在 Redis 持久化 8 位随机
  RUNNING_ID（形如 8 位字母数字）。实例下所有任务 `result_id = {RUNNING_ID}-{uuid4}`；
  `/check`、`/revoke` 都校验前缀必须匹配当前实例。
- **认证与连接**：HTTPS 自签证书（curl 加 `-k`）+ HTTP Basic Auth。实例连接信息
  （HOST/PORT/USER/PASSWD）通常由用户给出（环境变量或直接提供），不要到处猜密码。
- **典型端点**：`POST /create/{task}`、`GET /check/{task}?result_id=`、
  `POST /revoke`（body `{"result_id": "..."}`）、`POST /status_info`、`GET /openapi.json`、
  `POST /mcp`（MCP 协议端点，默认启用，供 AI 客户端调用）。
- **结果形态**：`/check` 与 `/run` 响应带 `result_type` 字段：
  - `json`：`result` 就是任务结果
  - `s3`：结果已外置到对象存储，`result` 是引用（含预签名 `url`）；
    **不要直接把 result 当结果用**，按 `result.url` 下载后再解析
  - `text`：`result` 是错误信息（失败时含完整 traceback）或状态文本

  客户端用 `fasttask_manager >= 0.6.0` 时会自动处理外置结果。

## 参考代码（用于翻阅真实实现，不涉及具体项目名/路径）

- 各 fasttask 部署项目的 `tasks/packages/exec_cmd.py` — subprocess 封装（随项目分发）
- FastTask 平台本体源码（含 api.py，revoke/check/status_info 精确语义以此为准）—
  本技能所在仓库的 fasttask/ 目录即平台源码
