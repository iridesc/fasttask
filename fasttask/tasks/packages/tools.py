import os
import time
from random import random
import hashlib
import inspect
import json
import redis
import pickle
from functools import wraps


cache_db = redis.Redis(
    host=os.environ["master_host"],
    port=os.environ["task_queue_port"],
    password=os.environ["task_queue_passwd"],
    db=0
)


def cache_result(ttl=3600):

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            key = f"{inspect.getmodule(func).__name__}.{func.__name__}:{json.dumps(args)}:{json.dumps(kwargs)}"
            key = hashlib.sha1(key.encode()).hexdigest()
            result = cache_db.get(key)
            if result:
                print("using cache!")
            else:
                print("no cache running...")

            result = pickle.loads(result) if result else func(*args, **kwargs)
            cache_db.set(key, pickle.dumps(result), ex=ttl)
            return result

        return wrapper
    return decorator


def xx(x):
    return x**2


def sleep_random():
    time.sleep(random() * 30)
