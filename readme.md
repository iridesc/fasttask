## FastTask

### desc
  科技以套壳为本
  
  使用fastapi 对celery的最简单封装, 仅暴露两个简单的接口
  1. 创建任务
  2. 检测任务(获取结果)

### 使用
1. 创建一个项目文件夹 project
2. 将 docker-compose.yml 下载到 project/docker-compose.yml
3. 创建project/tasks/
4. 创建一个异步任务模块 my_task.py
   ```
    from ..celery import app
    import time

    @app.task
    def add(x, y):
        print("task running...")
        time.sleep(5)
        r = x + y
        print("task done")
        return r 
   ```
5. 在项目文件夹中执行
   ```docker compose -f "docker-compose.yml" up -d ```

6. 访问 "" 查看接口文档

### 代办
1. 支持task 使用redis服务 来实现缓存之类的功能
2. 支持查看已经注册的任务
3. 依赖的注入