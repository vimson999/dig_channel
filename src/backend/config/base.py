# 基础配置文件
"""
基础配置类

包含所有环境通用的配置项
"""

import os
import sys
from dotenv import load_dotenv
from sqlalchemy.pool import NullPool

# 加载环境变量
load_dotenv()


def _coerce_int(value, default, minimum=0):
    try:
        value = int(value)
    except (TypeError, ValueError):
        value = default
    return max(value, minimum)


def _int_env(name, default, minimum=0):
    return _coerce_int(os.environ.get(name, default), default, minimum)


def _bool_env(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return bool(default)
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def _csv_env(name, default=''):
    value = os.environ.get(name, default)
    return [item.strip() for item in str(value or '').split(',') if item.strip()]


def _db_process_role():
    raw = (os.environ.get('DB_PROCESS_ROLE') or os.environ.get('APP_PROCESS_ROLE') or '').strip().lower()
    aliases = {
        'api': 'web',
        'backend': 'web',
        'flask': 'web',
        'web': 'web',
        'celery': 'worker',
        'worker': 'worker',
        'workers': 'worker',
        'scheduler': 'scheduler',
        'schedulers': 'scheduler',
        'watchdog': 'scheduler',
    }
    if raw in aliases:
        return aliases[raw]

    command = ' '.join(str(item) for item in sys.argv).lower()
    if 'celery' in command:
        return 'worker'
    if 'scheduler' in command or 'watchdog' in command:
        return 'scheduler'
    return 'web'


def _db_int_env(suffix, default, minimum=0):
    role_prefix = {
        'web': 'WEB',
        'worker': 'WORKER',
        'scheduler': 'SCHEDULER',
    }.get(_db_process_role())
    if role_prefix:
        role_name = f'{role_prefix}_DB_{suffix}'
        if os.environ.get(role_name) is not None:
            return _coerce_int(os.environ.get(role_name), default, minimum)
    return _int_env(f'DB_{suffix}', default, minimum)


def _db_text_env(suffix, default):
    role_prefix = {
        'web': 'WEB',
        'worker': 'WORKER',
        'scheduler': 'SCHEDULER',
    }.get(_db_process_role())
    if role_prefix:
        role_name = f'{role_prefix}_DB_{suffix}'
        value = os.environ.get(role_name)
        if value is not None:
            return str(value).strip().lower()
    value = os.environ.get(f'DB_{suffix}')
    return str(value if value is not None else default).strip().lower()


def _db_application_name():
    prefix = os.environ.get('DB_APPLICATION_NAME_PREFIX', 'dig_ytb')
    role = _db_process_role()
    command = os.path.basename(sys.argv[0] or 'python').replace('.', '_')
    raw = f'{prefix}_{role}_{command}_{os.getpid()}'
    return ''.join(ch if ch.isalnum() or ch in {'_', '-'} else '_' for ch in raw)[:63]


def _db_pool_size_default():
    return {
        'web': 4,
        'worker': 1,
        'scheduler': 1,
    }.get(_db_process_role(), 1)


def _db_pool_mode_default():
    return 'queue' if _db_process_role() == 'web' else 'null'


def _postgres_engine_options(
    database_uri,
    *,
    pool_size,
    max_overflow,
    pool_timeout,
    pool_recycle,
    connect_timeout,
    statement_timeout_ms=None,
    pool_mode='queue',
):
    if not str(database_uri or '').startswith(('postgresql', 'postgres://')):
        return {}

    connect_args = {
        'connect_timeout': connect_timeout,
        'sslmode': 'prefer',
        'application_name': _db_application_name(),
    }
    if statement_timeout_ms:
        connect_args['options'] = f'-c statement_timeout={statement_timeout_ms}'

    if str(pool_mode or '').strip().lower() in {'null', 'none', 'disabled', 'off'}:
        return {
            'poolclass': NullPool,
            'pool_pre_ping': True,
            'connect_args': connect_args,
        }

    return {
        'pool_size': pool_size,
        'max_overflow': max_overflow,
        'pool_timeout': pool_timeout,
        'pool_recycle': pool_recycle,
        'pool_pre_ping': True,
        'pool_use_lifo': True,
        'connect_args': connect_args,
    }

class BaseConfig:
    """基础配置类"""
    
    # 应用基本配置
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-in-production'
    AUTH_MODE = os.environ.get('AUTH_MODE', 'production').strip().lower()
    LOCAL_AUTH_BYPASS = _bool_env('LOCAL_AUTH_BYPASS', False)
    AUTH_REQUIRE_ADMIN = _bool_env('AUTH_REQUIRE_ADMIN', True)
    AUTH_VERSION_ENABLED = _bool_env('AUTH_VERSION_ENABLED', True)
    PUBLIC_REGISTRATION_ENABLED = _bool_env('PUBLIC_REGISTRATION_ENABLED', False)
    LOGIN_PORTAL_TOKEN = os.environ.get('LOGIN_PORTAL_TOKEN', '').strip()
    LOGIN_PORTAL_TTL_SECONDS = _int_env('LOGIN_PORTAL_TTL_SECONDS', 600, 60)
    PASSWORD_MIN_LENGTH = _int_env('PASSWORD_MIN_LENGTH', 14, 12)
    
    # 数据库配置
    basedir = os.path.abspath(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))  # 回到项目根目录
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or f'sqlite:///{os.path.join(basedir, "instance", "youtube_tracker.db")}'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ECHO = False
    
    # 数据库连接池配置。每个 Flask/Celery/scheduler 进程都有独立连接池，默认值要克制。
    SQLALCHEMY_ENGINE_OPTIONS = _postgres_engine_options(
        SQLALCHEMY_DATABASE_URI,
        pool_size=_db_int_env('POOL_SIZE', _db_pool_size_default(), 1),
        max_overflow=_db_int_env('MAX_OVERFLOW', 0, 0),
        pool_timeout=_db_int_env('POOL_TIMEOUT', 10, 1),
        pool_recycle=_db_int_env('POOL_RECYCLE', 1800, 60),
        connect_timeout=_db_int_env('CONNECT_TIMEOUT', 10, 1),
        statement_timeout_ms=os.environ.get('DB_STATEMENT_TIMEOUT_MS'),
        pool_mode=_db_text_env('POOL_MODE', _db_pool_mode_default()),
    )
    
    # Redis配置
    REDIS_HOST = os.environ.get('REDIS_HOST', 'localhost')
    REDIS_PORT = int(os.environ.get('REDIS_PORT', 6379))
    REDIS_DB = int(os.environ.get('REDIS_DB', 0))
    REDIS_URL = os.environ.get('REDIS_URL') or f"redis://{REDIS_HOST}:{REDIS_PORT}/{REDIS_DB}"
    
    # YouTube API配置
    YOUTUBE_API_KEY = os.environ.get('YOUTUBE_API_KEY')
    GOOGLE_CLIENT_ID = os.environ.get('GOOGLE_CLIENT_ID')
    GOOGLE_CLIENT_SECRET = os.environ.get('GOOGLE_CLIENT_SECRET')
    GOOGLE_OAUTH_CONFIG_FILE = os.environ.get('GOOGLE_OAUTH_CONFIG_FILE')
    GOOGLE_API_KEYS = os.environ.get('GOOGLE_API_KEYS')
    
    # Gemini API配置 (默认回退到 GOOGLE_API_KEYS)
    GOOGLE_GEMINI_KEYS = os.environ.get('GOOGLE_GEMINI_KEYS') or GOOGLE_API_KEYS
    
    # Bilibili配置
    BILI_SESSDATA = os.environ.get('BILI_SESSDATA')
    BILI_BILI_JCT = os.environ.get('BILI_BILI_JCT')
    BILI_BUVID3 = os.environ.get('BILI_BUVID3')
    BILI_DEDEUSERID = os.environ.get('BILI_DEDEUSERID')
    
    # JWT配置
    JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY') or SECRET_KEY
    JWT_ACCESS_TOKEN_EXPIRES = _int_env('JWT_ACCESS_TOKEN_EXPIRES', 900, 300)
    
    # Session配置
    SESSION_TYPE = 'filesystem'
    SESSION_PERMANENT = False
    PERMANENT_SESSION_LIFETIME = _int_env('PERMANENT_SESSION_LIFETIME', 8 * 3600, 900)
    SESSION_COOKIE_NAME = os.environ.get('SESSION_COOKIE_NAME', '__Host-shioa-session')
    SESSION_COOKIE_SECURE = _bool_env('SESSION_COOKIE_SECURE', True)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = os.environ.get('SESSION_COOKIE_SAMESITE', 'Strict')
    SESSION_COOKIE_PATH = '/'
    
    # 日志配置
    LOG_LEVEL = os.environ.get('LOG_LEVEL', 'INFO')
    LOG_DIR = os.path.join(basedir, 'logs')
    
    # 确保日志目录存在
    os.makedirs(LOG_DIR, exist_ok=True)

    # 可选功能开关
    _truthy = {'1', 'true', 'yes', 'on'}
    ENABLE_TTS_API = os.environ.get('ENABLE_TTS_API', 'false').lower() in _truthy
    ENABLE_MHTML_API = os.environ.get('ENABLE_MHTML_API', 'false').lower() in _truthy
    
    # API限流配置
    RATELIMIT_STORAGE_URL = (
        os.environ.get('RATELIMIT_STORAGE_URL')
        or os.environ.get('REDIS_URL')
        or REDIS_URL
    )
    RATELIMIT_STORAGE_URI = RATELIMIT_STORAGE_URL
    RATELIMIT_DEFAULT = os.environ.get('RATELIMIT_DEFAULT', '2000 per hour')
    LOGIN_IP_RATE_LIMIT = os.environ.get('LOGIN_IP_RATE_LIMIT', '30 per minute;300 per day')
    LOGIN_ACCOUNT_RATE_LIMIT = os.environ.get('LOGIN_ACCOUNT_RATE_LIMIT', '5 per 15 minutes')

    # 生产只允许显式同源列表。开发配置会覆盖为本机来源。
    CORS_ORIGINS = _csv_env('CORS_ORIGINS', '')
    TRUST_PROXY_HOPS = _int_env('TRUST_PROXY_HOPS', 1, 0)
    
    # 调试配置
    DEBUG = False
    TESTING = False
