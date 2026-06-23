FastTask 分布式任务平台

# 简介
    实现你的python函数，并以异步+分布式（可选）形式部署， 以提供权限控制的https接口可靠调用

# 特性
FastTask提供以下核心功能：

    1. 快速开发部署：定义输入输出， 实现你的函数， 打包镜像, 然后在任何容器化环境下部署

    2. 分布式： 轻松扩展，单节点 + 分布式部署，适用于各种规模和需求
 
    3. 通用交互： 使用fastapi + uvicorn 构建http api接口， 在任何语言环境调用

    4. 可靠的结果： rdb 持久化任务数据+执行时间限制， 即使在分布式场景， 极差的网络环境， 不稳定的宿主环境下， 仍然能有效保证任务结果， 对于异常任务会返回完整异常以定位业务问题

    5. 轻量： 基于python：slim， celery+redis, fasttapi+uvicorn

    6. 可控的任务类型执行： 通过ENABLED_TASKS， DISABLED_TASKS 控制具体某个节点可以执行/不可以执行那些任务


# 开始使用

1. 安装管理工具[fasttask_manager](https://github.com/iridesc/fasttask_manager)

    ```bash
    pip install fasttask_manager
    ```

2. 使用管理工具创建项目
    ```bash
    python -m fasttask_manager.create_project
    project name:test_project
    port (default:80):
     ```

    然后你会得到下面的目录结构
    ```bash
    ./test_project
    ├── docker-compose.yml
    ├── Dockerfile
    ├── requirements.txt # python依赖
    ├── setting.py 
    └── tasks
        ├── get_hypotenuse.py # 任务代码
        └── packages
            └── tools.py # 需要的一些工具函数
    ```
3. 增加依赖 !!

    在```test_project/requirements.txt```中增加你python的依赖
    在```test_project/Dockerfile``` 中增加你的环境依赖

4. 实现你的函数

    参考 ```get_hypotenuse.py```注意以下几点实现你的函数：

    - 任务文件名， 与内部的任务函数名需要保持一致， fasttask会自动注册函数到api接口
    - 这里 Params， Result 继承 BaseModel，Params中属性与你函数参数名一一致。 另外详细的输入输出定义有以下好处：
        - 会自动校验，以保证输出输出的确定性
        - 自动生成的接口文档页面会有详细的输入输出定义
    - 你的结果需要以 ```result.model_dump()```输出


    ```python
    from typing import Union
    from pydantic import BaseModel

    from packages.tools import xx, sleep_random


    class Params(BaseModel):
        a: Union[float, int]
        b: Union[float, int]


    class Result(BaseModel):
        hypotenuse: Union[float, int]


    def get_hypotenuse(a, b):
        if a <= 0 or b <= 0:
            raise ValueError("side length must > 0")
        print("running...")
        sleep_random()
        result = Result(hypotenuse=(xx(a) + xx(b))**0.5)
        return result.model_dump()
    ```


5. 打包运行
    ```bash
    docker build -t 'test_project:latest' . &&  docker compose up -d
    ```

6. 调用
    访问 ```https://localhost/docs```
    
    ![alt text](images/image.png)
    
    你会看到以下接口：

    - status_info:  返回服务状态信息（POST方法，body参数 `fields` 可选值：`worker_status`, `task_info`, `pending_task_count`）
    - download:  下载文件接口（GET方法，query参数 `file_name`）
    - upload:  上传文件接口（POST方法，上传文件）
    - revoke:  撤销任务接口（POST方法，body参数 `{result_id: "xxx"}`）
    - run/get_hypotenuse 同步调用你在tasks中实现的任务
    - create/get_hypotenuse 创建异步任务接口
    - check/get_hypotenuse 检测任务状态(获取任务结果)接口（GET方法，query参数 `result_id`）

    你可以：
    - 点击 ```try it out``` 直接填写参数调用
    - python代码通过[fasttask_manager](https://github.com/iridesc/fasttask_manager)调用
    - 其他代码直接请求接口

    以get_hypotenuse为例进行异步调用：
    - 在create 接口中填写参数 点击 execute后会拿到任务id
    ![alt text](images/image-1.png)
    ![alt text](images/image-2.png)
    - 在check接口中填写任务id 点击execute后，会返回任务结果
    ![alt text](images/image-3.png)

# 分布式部署

需要两个docker compose：
- master节点 

    ```yaml
    services:
    master:
        image: tes_project:latest
        container_name: fasttask-master
        restart: always

        ports:
        - "9001:443"
        - "9000:6379"

        volumes:
        - ./files-master:/fasttask/files

        environment:
        - NODE_TYPE=distributed_master
        - TASK_QUEUE_PASSWD=passwd
    ```

    - 6379为redis任务队列端口，其他worker需要连接到该端口
    - NODE_TYPE需要设置为distributed_master， 表示该节点为分布式master节点
    - TASK_QUEUE_PASSWD为redis密码，其他worker需要连接到该密码


- worker节点 
    ```yaml
    services:
    worker-get_hypotenuse:
        image: tes_project:test
        container_name: worker-get_hypotenuse
        restart: always

        volumes:
        - ./files-worker:/fasttask/files

        environment:
        - NODE_TYPE=distributed_worker
        - MASTER_HOST=10.65.8.8
        - TASK_QUEUE_PORT=9000
        - TASK_QUEUE_PASSWD=passwd

    ```
    - NODE_TYPE: 需要设置为distributed_worker
    - MASTER_HOST: master节点的ip
    - TASK_QUEUE_PORT: master节点的任务队列端口
    - TASK_QUEUE_PASSWD: master节点的任务队列密码

# 并发控制

FastTask 支持任务级别的并发控制。在调用任务时，可以通过 `fasttask_concurrency_params` 参数限制同一任务的并发执行数量。

参数说明：
- `concurrency_key`: 并发控制的标识key（必需），相同key的任务共享并发限制
- `max_concurrency`: 最大并发量（默认16）
- `countdown`: 获取锁失败后的退避等待时间（默认60秒）
- `expire`: 锁的过期时间（默认30分钟），避免死锁

使用示例：
```json
{
  "fasttask_concurrency_params": {
    "concurrency_key": "user_123",
    "max_concurrency": 5,
    "countdown": 30,
    "expire": 600
  },
  // 其他任务参数...
}
```

# Flower 监控

FastTask 集成了 [Flower](https://flower.readthedocs.io/) 作为 Celery 任务监控工具。通过设置 `FLOWER_ENABLED=True` 启用。

启用后可通过 `/flower` 路径访问 Flower Web UI 和 API：
- Web UI：`https://localhost/flower/` - 查看任务、Worker、队列状态
- API：`https://localhost/flower/api/workers`、`/flower/api/tasks` 等

认证说明：
- Flower 路径复用 FastTask 的 HTTP Basic 认证（`user_to_passwd.json`）
- 若未配置认证文件，则无需认证即可访问

相关配置：
- **FLOWER_ENABLED**：是否启用 Flower（默认 False）
- **FLOWER_PORT**：Flower 服务内部端口（默认 5555）
- **FLOWER_MAX_TASKS**：Flower 保留的最大任务数量（默认 5000）

# 认证

当存在有效的```files/fasttask/conf/user_to_passwd.json```时， 自动启用认证功能， 文件内容参考：
```json
{
"user_A": "user_A_passwd",
"user_B": "user_B_passwd"
}
```

# 文件
你可以通过upload接口上传你任务中所必要的文件，这个文件被放在```/fasttask/files/0caee52c-b2ca-4c04-b040-82bd952192da_1.xlsx```目录下， 你的任务代码可以打开并处理文件

当任务结果需要输出到文件时， 你可以把文件保存在```/fasttask/files/0caee52c-b2ca-4c04-b040-82bd952192da_1.xlsx```， 然后你可以调用download接口下载文件。

## 文件过期清理

FastTask 内建文件自动过期删除机制，由 Supervisor 管理的独立进程负责执行。

清理规则：
- 基于文件最后修改时间（mtime）判断是否过期，过期后直接删除
- 递归扫描 `files/` 下所有子目录（跳过 `files/fasttask/` 系统目录）
- 文件删除后若所在目录为空，一并清理空目录
- 每 10 分钟执行一次清理扫描

相关配置：
- **FILE_CLEANUP_ENABLED**：是否启用文件清理（默认 `True`）。设为 `False` 时，清理进程不会启动
- **FILE_EXPIRATION_SECONDS**：文件过期时间（秒），默认为 `SOFT_TIME_LIMIT` × 10（约 10 天）。启用时最小值为 60 秒


# 核心配置

- **SOFT_TIME_LIMIT**： 运行时间限制， 单位秒， 配置时默认为1天， 超过该时间任务进程会被直接杀死， 任务状态会变为失败
- **TIME_LIMIT**： 硬超时时间， 单位秒， 默认为 SOFT_TIME_LIMIT + 60秒， 任务达到此时间会被强制终止
- **VISIBILITY_TIMEOUT**： Celery broker 可见性超时， 单位秒， 默认为 TIME_LIMIT + 60秒， 任务在此时间内未被处理会重新入队
- **RESULT_EXPIRES**： 结果过期时间， 单位秒， 配置时默认为3天， 超过该时间任务结果会被删除
- **WORKER_CONCURRENCY**： 并发数， 默认为cpu核数
- **WORKER_POOL**： Worker池类型， 默认为 `prefork`， 可选 `gevent`（需安装 gevent）
- **DEBUG**： 是否启用调试模式， 默认为 False， 启用后会打印详细请求/响应日志
- **API_x**： 接口是否启用配置，默认为 True，比如 API_RUN为 False时, 所有任务的run接口都会被禁用
- **ENABLED_TASKS**： 逗号分隔的任务名称列表（例如 get_circle_area,get_hypotenuse）。如果设置，此 Worker 只会处理这些指定的任务。优先级高于 DISABLED_TASKS。
- **DISABLED_TASKS**：逗号分隔的任务名称列表。如果设置，此 Worker 将不处理这些指定的任务。
- **FLOWER_ENABLED**：是否启用 Flower 监控服务（默认 False）
- **FLOWER_PORT**：Flower 服务内部端口（默认 5555）
- **FLOWER_MAX_TASKS**：Flower 保留的最大任务数量（默认 5000）
- **WORKER_TAG**：Worker 标识标签，用于区分不同 Worker（默认 "worker")
- **FILE_CLEANUP_ENABLED**：是否启用文件过期清理（默认 `True`）。设为 `False` 则清理进程不启动
- **FILE_EXPIRATION_SECONDS**：文件过期时间（秒），默认为 `SOFT_TIME_LIMIT` × 10。启用时最小值为 60 秒

更多配置参考[./fasttask/run.py](https://github.com/iridesc/fasttask/blob/main/fasttask/run.py) env_type_to_envs

# 更新日志

## 2025-06
- **新增文件过期清理机制**：独立 Supervisor 进程定期扫描 `files/` 目录，基于 mtime 删除过期文件和空目录，跳过 `files/fasttask/` 系统目录
- **新增配置项**：`FILE_CLEANUP_ENABLED`、`FILE_EXPIRATION_SECONDS`
- **重构 Supervisor 配置管理**：从单体配置文件改为模板组装模式（`supervisord_template_conf/` → `supervisord_conf/`），根据 `NODE_TYPE`、`FLOWER_ENABLED`、`FILE_CLEANUP_ENABLED` 动态组装
- **优化 Supervisor 日志输出**：所有日志直接打到 stdout/stderr（`logfile=/dev/stdout`、`pidfile=/dev/null`），不再写本地文件，方便 `docker logs` 查看
- **修复 Flower 代理中间件**：内部代理请求不走系统代理环境变量（`trust_env=False`），强制使用 IPv4（`127.0.0.1`）

## 2025-05
- **新增 Flower 监控支持**：集成 Flower 作为 Celery 任务监控工具，可通过 `/flower` 路径访问 Web UI 和 API
- **新增配置项**：`FLOWER_ENABLED`、`FLOWER_PORT`、`FLOWER_MAX_TASKS`、`WORKER_TAG`
- **修复 ENABLED_TASKS/DISABLED_TASKS**：修复任务启用/禁用逻辑未生效的问题
- **优化并发控制**：并发锁 key 增加 `fasttask:lock:` 前缀防止冲突，参数校验优化
- **优化 Redis 连接**：`timeout` 改为 0（不主动断开），增加 `socket_keepalive`、`retry_on_timeout`，健康检查间隔缩短为 20 秒
- **优化错误信息**：任务被 kill 时返回更完整的错误信息

# todo
- 在认证通过前 不展示 docs页面
- check 接口增加任务创建 更新时间
