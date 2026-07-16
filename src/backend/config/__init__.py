# 配置包初始化
import os
from .base import BaseConfig
from .development import DevelopmentConfig
from .production import ProductionConfig

config_map = {
    'development': DevelopmentConfig,
    'testing': BaseConfig,
    'production': ProductionConfig,
}

_environment = os.environ.get('FLASK_ENV', 'development').strip().lower()
Config = config_map.get(_environment, DevelopmentConfig)

def get_config():
    """获取当前环境配置"""
    return Config()
