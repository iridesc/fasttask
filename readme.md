FastTask 分布式任务平台

FastTask 是一个强大且灵活的分布式任务平台，旨在简化异步任务处理和分布式计算。它基于 Celery 和 FastAPI 构建，提供了从任务下发、执行到结果查询的完整解决方案，并支持单节点和分布式部署，以适应从小规模应用到大规模生产环境的各种需求。
项目目的与功能

FastTask 旨在解决传统任务处理中的痛点，提供以下核心功能：

    异步任务执行： 将耗时操作封装为异步任务，避免阻塞主应用线程，提升用户体验和系统响应速度。
    任务队列管理： 利用 Redis 作为消息代理，实现任务的持久化、调度和分发，确保任务的可靠执行。
    分布式扩展： 轻松扩展 Worker 节点，将任务负载分布到多台服务器上，实现高并发和高吞吐量。
    API 驱动： 提供 FastAPI 接口，方便地通过 HTTP 请求进行任务的创建、查询、撤销等操作，易于集成到现有系统中。
    任务超时管理： 支持软超时和硬超时，确保任务不会无限期运行，有效管理资源。
    文件上传下载： 提供文件管理 API，方便任务处理过程中的文件交互。

技术栈

FastTask 主要基于以下成熟且广泛使用的技术构建：

    FastAPI： 现代、快速（高性能）的 Web 框架，用于构建任务 API 接口。
    Celery： 强大的分布式任务队列，用于异步任务的调度和执行。
    Redis： 高性能的键值存储数据库，用作 Celery 的消息代理 (Broker) 和结果后端 (Backend)。
    Docker / Docker Compose： 用于容器化部署，简化环境配置和依赖管理。
    Uvicorn： ASGI 服务器，用于运行 FastAPI 应用。
    Supervisor： 进程管理系统，确保 Celery Worker 和 Uvicorn 进程的稳定运行。
    Python： 整个项目的开发语言。

使用方法

FastTask 可以通过 Docker Compose 进行部署。根据你的需求，可以选择单节点模式或分布式模式。
部署方式
1. 单节点模式 (Single Node)

适用于开发、测试或小型应用，所有组件（API、Redis Broker、Celery Worker）运行在同一个容器内。

docker-compose.yml 配置示例：


2. 分布式模式 (Distributed)

适用于生产环境，Master 节点提供 API 和 Redis Broker 服务，Worker 节点独立运行并连接到 Master。Worker 节点可以部署在同一台主机或不同主机上。


访问 API

API 服务通常会在宿主机的 9001 端口上运行。

    API 文档 (Swagger UI): https://localhost:9001/docs
    API 文档 (Redoc): https://localhost:9001/redoc

示例：创建并下发 get_circle_area 任务

使用 curl 或任何 HTTP 客户端向 /create/get_circle_area 接口发送 POST 请求：
Bash

curl -k -X POST "https://localhost:9001/create/get_circle_area" \
     -H "accept: application/json" \
     -H "Content-Type: application/json" \
     -d "{\"radius\": 5.0}"

示例：查询任务状态

假设任务创建成功后返回的 task_id 为 your_task_id。
Bash

curl -k "https://localhost:9001/check/your_task_id"

环境变量详解

FastTask 的环境变量通过 run.py 脚本中的 Env 类进行管理，支持默认值、强制默认值以及基于其他环境变量的动态值。
通用环境变量 (Common)

这些变量在所有 NODE_TYPE 下都适用。
环境变量	默认值/示例	描述
RUNNING_ID	uuid.uuid4()	当前 FastTask 实例的唯一标识符。用于区分日志和任务 ID。
NODE_TYPE	无默认值	【必需】 定义当前运行节点的角色。可选值：single_node, distributed_master, distributed_worker。
SOFT_TIME_LIMIT	1800 (30 分钟)	任务的软时间限制（秒）。任务超时此时间会抛出 SoftTimeLimitExceeded 异常，允许任务在被强制终止前执行清理或保存进度。
TIME_LIMIT	SOFT_TIME_LIMIT + 60	任务的硬时间限制（秒）。任务超时此时间会被 Celery Worker 强制终止进程。必须大于 SOFT_TIME_LIMIT。
VISIBILITY_TIMEOUT	TIME_LIMIT + 60	Celery 任务在 Redis Broker 中的可见性超时（秒）。若 Worker 在此时间内未确认任务，任务会被重新排队。必须大于 TIME_LIMIT。
LOADED_TASKS	动态加载 tasks/ 目录下的任务名称 (逗号分隔)	自动发现并加载应用中可用的任务。通常由系统自动设置。
FASTTASK_DIR	/fasttask	FastTask 应用的根目录。
FILES_DIR	/fasttask/files	应用程序文件（如日志、配置文件、上传下载文件）的根目录。会在启动时自动创建。
FASTTASK_FILES_DIR	/fasttask/files/fasttask	FastTask 专用文件目录。
LOG_DIR	/fasttask/files/fasttask/log	日志文件存放目录。
CONF_DIR	/fasttask/files/fasttask/conf	配置文件存放目录。
SSL_CERT_DIR	/fasttask/files/fasttask/ssl_cert	SSL 证书存放目录。如果证书文件不存在，run.py 会自动生成自签名证书。
SSL_KEYFILE	/fasttask/files/fasttask/ssl_cert/key.pem	SSL 私钥文件完整路径。
SSL_CERTFILE	/fasttask/files/fasttask/ssl_cert/cert.pem	SSL 证书文件完整路径。
CELERY_DIR	/fasttask/files/fasttask/celery	Celery 运行时相关文件（如进程 ID 文件）的存放目录。
WORKER_POOL	prefork	Celery Worker 进程池的类型。prefork 使用多进程，适合 CPU 密集型任务；gevent 适合 I/O 密集型任务。
WORKER_CONCURRENCY	os.cpu_count() (CPU 核心数)	Celery Worker 并发执行任务的数量（进程或线程数，取决于 WORKER_POOL）。
单节点模式 (NODE_TYPE=single_node)

当 NODE_TYPE 设置为 single_node 时，FastTask 作为一个独立的单元运行，同时提供 API 服务和任务执行能力。
环境变量	默认值/示例	描述
MASTER_HOST	0.0.0.0	Celery Broker (Redis) 在容器内部监听的 IP 地址。对于 Master 容器内部的 API 服务，建议显式设置为 127.0.0.1 以确保本地连接的稳定性。
TASK_QUEUE_PORT	6379	Celery Broker (Redis) 在容器内部监听的端口。
TASK_QUEUE_PASSWD	passwd	Celery Broker (Redis) 的访问密码。
UVICORN_WORKERS	os.cpu_count()	FastAPI 应用的 Uvicorn Web 服务器工作进程数。
API_DOCS	True	是否启用 Swagger UI 接口文档 (/docs)。
API_REDOC	True	是否启用 Redoc 接口文档 (/redoc)。
API_STATUS_INFO	True	是否启用 /status_info 接口，提供任务统计信息。
API_RUN	True	是否启用 /run/{task_name} 接口，用于同步执行任务。
API_CREATE	True	是否启用 /create/{task_name} 接口，用于异步创建和下发任务。
API_CHECK	True	是否启用 /check/{task_name} 接口，用于查询任务状态和结果。
API_REVOKE	True	是否启用 /revoke 接口，用于撤销正在运行的任务。
API_FILE_DOWNLOAD	True	是否启用 /download 接口，用于文件下载。
API_FILE_UPLOAD	True	是否启用 /upload 接口，用于文件上传。
RESULT_EXPIRES	86400 (24 小时)	Celery 任务结果在 Redis 中存储的过期时间（秒）。
分布式主节点模式 (NODE_TYPE=distributed_master)

当 NODE_TYPE 设置为 distributed_master 时，此节点将作为分布式部署的核心，提供 API 服务并运行 Celery Broker (Redis)。Worker 节点将连接到此节点的 Redis 服务。
环境变量	默认值/示例	描述
MASTER_HOST	0.0.0.0	Celery Broker (Redis) 在容器内部监听的 IP 地址。对于 Master 容器内部的 API 服务，推荐明确设置为 127.0.0.1，以确保通过本地环回地址连接容器内部的 Redis。0.0.0.0 通常用于监听，而非客户端连接。
TASK_QUEUE_PORT	6379	Celery Broker (Redis) 在容器内部监听的端口。
TASK_QUEUE_PASSWD	无默认值	【必需】 Celery Broker (Redis) 的访问密码。Worker 节点需要使用相同的密码才能连接。
UVICORN_WORKERS	os.cpu_count()	FastAPI 应用的 Uvicorn Web 服务器工作进程数。
API_DOCS	True	(同 single_node)
API_REDOC	True	(同 single_node)
API_STATUS_INFO	True	(同 single_node)
API_RUN	True	(同 single_node)
API_CREATE	True	(同 single_node)
API_CHECK	True	(同 single_node)
API_REVOKE	True	(同 single_node)
API_FILE_DOWNLOAD	True	(同 single_node)
API_FILE_UPLOAD	True	(同 single_node)
分布式从节点模式 (NODE_TYPE=distributed_worker)

当 NODE_TYPE 设置为 distributed_worker 时，此节点将作为 Celery Worker 运行，连接到 Master 节点上的 Celery Broker，并执行分配给它的任务。
环境变量	默认值/示例	描述
MASTER_HOST	无默认值	【必需】 Celery Broker (Master 节点) 的 IP 地址。如果 Worker 与 Master 在同一台 Docker 主机上，且 Master 的 Redis 端口已映射到宿主机，则此处应填写 Master 宿主机的 IP 地址（例如 10.65.8.8）。如果 Master 在 Docker Compose 内部网络中，且 Worker 也在同一网络，则应填写 Master 服务的名称（例如 master）。
TASK_QUEUE_PORT	无默认值	【必需】 Celery Broker (Master 节点) 监听的端口。这应是 Worker 能够访问到的 Master 宿主机上映射的 Redis 端口（例如 9000），而不是 Master 容器内部的 Redis 端口（6379）。
TASK_QUEUE_PASSWD	无默认值	【必需】 Celery Broker (Redis) 的访问密码。必须与 Master 节点上配置的密码一致。
RESULT_EXPIRES	86400 (24 小时)	Celery 任务结果在 Redis 中存储的过期时间（秒）。
ENABLED_TASKS	无	逗号分隔的任务名称列表（例如 get_circle_area,get_hypotenuse）。如果设置，此 Worker 只会处理这些指定的任务。优先级高于 DISABLED_TASKS。
DISABLED_TASKS	无	逗号分隔的任务名称列表。如果设置，此 Worker 将不处理这些指定的任务。