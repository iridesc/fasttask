# _*_coding:utf-8_*_
"""
@author: liangwenpeng
@date: 2025/9/11
"""
import os

import redis
import time

redis_params = {
    "host": os.environ["MASTER_HOST"],
    "port": os.environ["TASK_QUEUE_PORT"],
    "password": os.environ["TASK_QUEUE_PASSWD"],
    "decode_responses": True,
    "socket_connect_timeout": 5,
}


class ConcurrencyLimitExceeded(Exception):
    """并发超过限制时抛出的异常"""
    pass


class RedisConcurrencyController:
    def __init__(self, max_concurrent=5, expire=10):
        self.redis = redis.StrictRedis(**redis_params)
        self.max_concurrent = max_concurrent
        self.expire = expire

    def acquire(self, resource_key, raise_exection=False):
        """
        尝试获取一个资源的并发许可。
        :param resource_key: 资源唯一Key
        :return: True 获取成功, False 被限流或冲突直接失败
        """
        key = f"concurrency:{resource_key}"
        with self.redis.pipeline() as pipe:
            try:
                pipe.watch(key)
                count = pipe.get(key)
                count = int(count) if count else 0
                if count >= self.max_concurrent:
                    pipe.unwatch()
                    return False
                pipe.multi()
                pipe.incr(key, 1)
                pipe.expire(key, self.expire)
                pipe.execute()
                return True
            except redis.WatchError:
                # 发生冲突，立即失败
                if raise_exection:
                    raise ConcurrencyLimitExceeded(f"{resource_key} 并发数超限或冲突，立即失败")
                return False

    def release(self, resource_key):
        key = f"concurrency:{resource_key}"
        self.redis.decr(key)


# 使用示例
if __name__ == '__main__':
    controller = RedisConcurrencyController(max_concurrent=3, expire=5)
    resource = "api:foo"

    if controller.acquire(resource):
        print("获取到并发许可，执行任务")
        time.sleep(2)
        controller.release(resource)
        print("任务完成，释放许可")
    else:
        print("并发数超限或冲突，立即失败")
