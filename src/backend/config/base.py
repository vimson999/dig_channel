# 基础配置文件
"""
基础配置类

包含所有环境通用的配置项
"""

import os
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

class BaseConfig:
    """基础配置类"""
    
    # 应用基本配置
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    
    # 数据库配置
    basedir = os.path.abspath(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))  # 回到项目根目录
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or f'sqlite:///{os.path.join(basedir, "instance", "youtube_tracker.db")}'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ECHO = False
    
    # 数据库连接池配置
    SQLALCHEMY_ENGINE_OPTIONS = {
        'pool_size': 20,           # 连接池大小
        'max_overflow': 30,        # 最大溢出连接数
        'pool_timeout': 30,        # 获取连接的超时时间
        'pool_recycle': 1800,      # 连接回收时间(30分钟)
        'pool_pre_ping': True,     # 连接前ping检查
        'connect_args': {
            'connect_timeout': 10,  # 连接超时
            'sslmode': 'prefer',    # SSL模式
        } if os.environ.get('DATABASE_URL', '').startswith('postgresql') else {}
    }
    
    # Redis配置
    REDIS_HOST = os.environ.get('REDIS_HOST', 'localhost')
    REDIS_PORT = int(os.environ.get('REDIS_PORT', 6379))
    REDIS_DB = int(os.environ.get('REDIS_DB', 0))
    REDIS_URL = f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
    
    # YouTube API配置
    YOUTUBE_API_KEY = os.environ.get('YOUTUBE_API_KEY')
    GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
    GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')
    GOOGLE_OAUTH_CONFIG_FILE = os.environ.get('GOOGLE_OAUTH_CONFIG_FILE')
    
    # JWT配置
    JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY') or SECRET_KEY
    JWT_ACCESS_TOKEN_EXPIRES = 3600  # 1小时
    
    # Session配置
    SESSION_TYPE = 'filesystem'
    SESSION_PERMANENT = False
    PERMANENT_SESSION_LIFETIME = 7 * 24 * 3600  # 7天
    
    # 日志配置
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')
    LOG_DIR = os.path.join(basedir, 'logs')
    
    # 确保日志目录存在
    os.makedirs(LOG_DIR, exist_ok=True)
    
    # API限流配置
    RATELIMIT_STORAGE_URL = REDIS_URL
    RATELIMIT_DEFAULT = "200 per day;50 per hour"
    
    # 调试配置
    DEBUG = False
    TESTING = False