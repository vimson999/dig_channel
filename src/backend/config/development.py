# 开发环境配置
"""
开发环境配置类

用于本地开发环境的特定配置
"""

import os

from .base import BaseConfig, _int_env, _postgres_engine_options

class DevelopmentConfig(BaseConfig):
    """开发环境配置"""
    
    DEBUG = True
    SQLALCHEMY_ECHO = True  # 开发环境下显示SQL语句
    
    # 开发环境下放宽CORS限制
    CORS_ORIGINS = ["*"]
    
    # 开发环境下的特殊设置
    TESTING = False
    
    # 调试工具栏
    DEBUG_TB_ENABLED = True
    DEBUG_TB_INTERCEPT_REDIRECTS = False
    
    # 开发环境通常同时跑后端、多个 Celery worker 和 scheduler；每个进程独立建池。
    # 本地队列较多时连接池会成倍放大，默认只保留 1 个连接，避免打满远端 PostgreSQL。
    SQLALCHEMY_ENGINE_OPTIONS = _postgres_engine_options(
        BaseConfig.SQLALCHEMY_DATABASE_URI,
        pool_size=_int_env('DB_POOL_SIZE', 1, 1),
        max_overflow=_int_env('DB_MAX_OVERFLOW', 0, 0),
        pool_timeout=_int_env('DB_POOL_TIMEOUT', 10, 1),
        pool_recycle=_int_env('DB_POOL_RECYCLE', 1800, 60),
        connect_timeout=_int_env('DB_CONNECT_TIMEOUT', 5, 1),
        statement_timeout_ms=os.environ.get('DB_STATEMENT_TIMEOUT_MS', '30000'),
    )
