## FastTask

### desc
  科技以套壳为本
  
  使用fastapi 对celery的最简单封装, 仅暴露两个简单的接口
  1. 创建任务
  2. 检测任务(获取结果)

### 使用
···

### 代办
1. 支持task 使用redis服务 来实现缓存之类的功能
2. 支持查看已经注册的任务
3. ~~依赖的安装~~
4. 创建任务时 需要增加异常处理
5. 工作如今问题 celery_task/tasks/***
6. 支持分布式结构
7. 支持文档页面的项目名称展示
8. ~~支持 worker数量在创建项目时配置~~
9. 支持任务的输入输出直接展示在页面api接口中
10. ~~创建项目时支持指定端口~~
11. 支持同步任务执行的接口
12. 支持选择性配置是否启用同步任务接口
