## FastTask

### desc
  科技以套壳为本
  
  使用fastapi 对celery的最简单封装, 仅暴露两个简单的接口
  1. 创建任务
  2. 检测任务(获取结果)

### 使用

1. 下载fasttask_demo到你的本地
2. 修改fasttask_demo为你的项目名称
3. 在req.txt添加你的项目的依赖
4. 如果需要， 修改Dockerfile初始化容器环境 
5. 修改
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
6. 在项目文件夹中执行
   ```docker compose -f "docker-compose.yml" up -d ```

7. 访问 "" 查看接口文档

### 代办
1. 支持task 使用redis服务 来实现缓存之类的功能
2. 支持查看已经注册的任务
3. ~~依赖的安装~~
4. 创建任务时 需要增加异常处理