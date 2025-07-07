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