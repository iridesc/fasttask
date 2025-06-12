## FastTask

### 描述
  科技以套壳为本
  
  使用 FastAPI 对 Celery 进行简单封装，提供以下功能：
  - 5 个任务接口（创建任务、检测任务(获取结果)、同步调用任务）
  - 1 个系统状态信息接口
  - 1 个撤销任务接口
  - 2 个文件接口
  - 支持通过 HTTP Basic 认证进行权限控制
  - 详细接口文档请参考 [FastAPI 文档](http://localhost:8005/docs) 或 [ReDoc 文档](http://localhost:8005/redoc)


### 环境变量配置
- `MASTER_HOST`：Redis 主机地址。
- `TASK_QUEUE_PORT`：Redis 端口。
- `task_queue_passwd`：Redis 密码。
- `API_DOCS`：是否启用 API 文档。
- `API_REDOC`：是否启用 ReDoc 文档。
- `API_STATUS_INFO`：是否启用状态信息接口。
- `API_FILE_DOWNLOAD`：是否启用文件下载接口。
- `API_FILE_UPLOAD`：是否启用文件上传接口。
- `API_REVOKE`：是否启用撤销任务接口。
- `API_RUN`：是否启用同步调用任务接口。
- `API_CREATE`：是否启用创建任务接口。
- `api_check`：是否启用检测任务接口。

### 运行和测试
- 使用 `docker-compose.yml` 文件启动服务：`docker-compose up -d`。
- 使用 `fasttask/run.sh` 脚本启动服务：`./fasttask/run.sh`。

### API 端点和功能描述
- `/status_info`：获取系统状态信息。
- `/download`：文件下载接口。
- `/upload`：文件上传接口。
- `/revoke`：撤销任务接口。
- `/run/{task_name}`：同步调用任务接口。
- `/create/{task_name}`：创建任务接口。
- `/check/{task_name}`：检测任务（获取结果）接口。


### 配置文件
- `setting.py`：包含项目的基本配置信息，如项目标题、描述、版本等。
- `files/user_to_passwd.json`：包含用户名和密码的 JSON 文件，用于 HTTP Basic 认证。

### 运行项目
1. **启动服务**：
   - 使用 `docker-compose.yml` 文件启动服务：
     ```
     docker-compose up -d
     ```
 3. **测试 API**：
   - 打开浏览器访问 [FastAPI 文档](http://localhost:8005/docs) 或 [ReDoc 文档](http://localhost:8005/redoc) 查看详细接口信息。
   - 使用 `curl` 或 Postman 等工具测试 API 接口。

### 任务示例
- **get_circle_area**：计算圆的面积。
  - **参数**：
    - `r`：圆的半径（必须大于 0）。
  - **返回值**：
    - `area`：圆的面积。

- **创建任务**：
  - **请求**：
    ```
    POST /create/get_circle_area
    Content-Type: application/json
    Authorization: Basic <base64_encoded_username_password>
    {
      "r": 5
    }
    ```
  - **响应**：
    ```
    {
      "id": "task_id",
      "state": "PENDING",
      "result": ""
    }
    ```

- **检测任务**：
  - **请求**：
    ```
    GET /check/get_circle_area?result_id=task_id
    Authorization: Basic <base64_encoded_username_password>
    ```
  - **响应**：
    ```
    {
      "id": "task_id",
      "state": "SUCCESS",
      "result": {
        "area": 78.53981633974483
      }
    }
    ```

- **同步调用任务**：
  - **请求**：
    ```
    POST /run/get_circle_area
    Content-Type: application/json
    Authorization: Basic <base64_encoded_username_password>
    {
      "r": 5
    }
    ```
  - **响应**：
    ```
    {
      "id": "",
      "state": "SUCCESS",
      "result": {
        "area": 78.53981633974483
      }
    }
    ```

### 权限控制
- 使用 HTTP Basic 认证进行权限控制。
- 用户名和密码存储在 `./files/user_to_passwd.json` 文件中。

### 日志和调试
- 日志信息在控制台输出。
- 可以通过 `API_STATUS_INFO` 接口查看任务状态信息。

