import redis
import json
import logging
import os
from functools import wraps
from config.base import BaseConfig

logger = logging.getLogger(__name__)

# 初始化 Redis 客户端
redis_client = redis.Redis.from_url(
    os.environ.get("REDIS_URL") or BaseConfig.REDIS_URL,
    decode_responses=True,
    socket_connect_timeout=2,
    socket_timeout=2,
)

def cache_response(timeout=3600, key_prefix="cache"):
    """
    Redis 缓存装饰器
    :param timeout: 缓存过期时间（秒），默认 1 小时
    :param key_prefix: 缓存键前缀
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # 构建缓存键: prefix:func_name:args:kwargs
            cache_key = f"{key_prefix}:{f.__name__}:{str(args)}:{str(kwargs)}"
            
            try:
                # 尝试获取缓存
                cached_data = redis_client.get(cache_key)
                if cached_data:
                    return json.loads(cached_data)
            except Exception as e:
                logger.error(f"Redis cache get error: {e}")

            # 执行原函数
            response = f(*args, **kwargs)

            try:
                # 存入缓存
                redis_client.setex(
                    cache_key,
                    timeout,
                    json.dumps(response)
                )
            except Exception as e:
                logger.error(f"Redis cache set error: {e}")

            return response
        return decorated_function
    return decorator
