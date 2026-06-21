# 开发环境配置
"""
开发环境配置类

用于本地开发环境的特定配置
"""

import os

from .base import BaseConfig, _db_int_env, _db_process_role, _postgres_engine_options


def _development_pool_size_default():
    return {
        'web': 4,
        'worker': 1,
        'scheduler': 1,
    }.get(_db_process_role(), 1)

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
    
    # 开发环境经常同时跑后端、Celery worker 和计划任务；每个进程都有独立连接池。
    # 本地默认按角色分配：Web 后端给少量并发余量，worker/scheduler 维持单连接。
    SQLALCHEMY_ENGINE_OPTIONS = _postgres_engine_options(
        BaseConfig.SQLALCHEMY_DATABASE_URI,
        pool_size=_db_int_env('POOL_SIZE', _development_pool_size_default(), 1),
        max_overflow=_db_int_env('MAX_OVERFLOW', 0, 0),
        pool_timeout=_db_int_env('POOL_TIMEOUT', 10, 1),
        pool_recycle=_db_int_env('POOL_RECYCLE', 1800, 60),
        connect_timeout=_db_int_env('CONNECT_TIMEOUT', 5, 1),
        statement_timeout_ms=os.environ.get('DB_STATEMENT_TIMEOUT_MS', '30000'),
    )
