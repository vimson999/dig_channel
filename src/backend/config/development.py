# 开发环境配置
"""
开发环境配置类

用于本地开发环境的特定配置
"""

from .base import BaseConfig

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
    
    # 开发环境下更宽松的数据库连接配置
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': 10,           # 开发环境较小的连接池
        'max_overflow': 20,        # 较少的溢出连接
        'pool_timeout': 30,        # 获取连接的超时时间
        'pool_recycle': 3600,      # 连接回收时间(1小时)
        'pool_pre_ping': True,     # 连接前ping检查
        'connect_args': {
            'connect_timeout': 5,   # 较短的连接超时
            'sslmode': 'prefer',    # SSL模式
            'options': '-c statement_timeout=30000',  # SQL语句超时30秒
        } if BaseConfig.SQLALCHEMY_DATABASE_URI.startswith('postgresql') else {}
    }