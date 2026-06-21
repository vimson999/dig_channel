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
    SQLALCHEMY_ECHO = os.environ.get('SQLALCHEMY_ECHO', '0').strip().lower() in {'1', 'true', 'yes', 'on'}
    
    # 开发环境下放宽CORS限制
    CORS_ORIGINS = ["*"]
    
    # 开发环境下的特殊设置
    TESTING = False
    
    # 调试工具栏
    DEBUG_TB_ENABLED = True
    DEBUG_TB_INTERCEPT_REDIRECTS = False
    
    # 开发环境经常同时跑后端和多个 Celery worker；每个进程都有独立连接池。
    # 默认只保留 1 个连接，避免本地进程把远端 PostgreSQL 连接槽打满。
    SQLALCHEMY_ENGINE_OPTIONS = _postgres_engine_options(
        BaseConfig.SQLALCHEMY_DATABASE_URI,
        pool_size=_int_env('DB_POOL_SIZE', 1, 1),
        max_overflow=_int_env('DB_MAX_OVERFLOW', 0, 0),
        pool_timeout=_int_env('DB_POOL_TIMEOUT', 10, 1),
        pool_recycle=_int_env('DB_POOL_RECYCLE', 1800, 60),
        connect_timeout=_int_env('DB_CONNECT_TIMEOUT', 5, 1),
        statement_timeout_ms=os.environ.get('DB_STATEMENT_TIMEOUT_MS', '30000'),
    )
