# 配置包初始化
import os
from .base import BaseConfig
from .development import DevelopmentConfig

config_map = {
    'development': DevelopmentConfig,
    'testing': BaseConfig,
    'production': BaseConfig,
}

Config = config_map.get(os.environ.get('FLASK_ENV', 'development'), DevelopmentConfig)

def get_config():
    """获取当前环境配置"""
    return Config()