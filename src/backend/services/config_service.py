import os
import threading
import time
from models import db, SystemConfig
from flask import current_app


CONFIG_SERVICE_CACHE_TTL_SECONDS = float(os.environ.get('CONFIG_SERVICE_CACHE_TTL', '60'))
_CONFIG_CACHE = {}
_CONFIG_CACHE_LOCK = threading.Lock()


def _cache_get(cache_key):
    if CONFIG_SERVICE_CACHE_TTL_SECONDS <= 0:
        return None, False
    with _CONFIG_CACHE_LOCK:
        cached = _CONFIG_CACHE.get(cache_key)
        if not cached or cached.get('expires_at', 0) <= time.time():
            return None, False
        return cached.get('value'), True


def _cache_set(cache_key, value):
    if CONFIG_SERVICE_CACHE_TTL_SECONDS <= 0:
        return
    with _CONFIG_CACHE_LOCK:
        _CONFIG_CACHE[cache_key] = {
            'expires_at': time.time() + CONFIG_SERVICE_CACHE_TTL_SECONDS,
            'value': value,
        }


def _cache_clear():
    with _CONFIG_CACHE_LOCK:
        _CONFIG_CACHE.clear()

class ConfigService:
    """
    配置管理服务
    
    统一管理系统配置的读取。
    优先级: 数据库(当前环境) > 环境变量(.env) > 默认值
    """
    
    @staticmethod
    def get_current_scope():
        """获取当前运行环境 (scope)"""
        # 默认从环境变量 FLASK_ENV 读取，如果没有则默认为 production
        return os.environ.get('FLASK_ENV', 'production')

    @staticmethod
    def get(key, type_group=None, default=None):
        """
        获取配置值
        
        Args:
            key: 配置键名 (如 'sessdata')
            type_group: (可选) 业务类型，用于缩小查询范围 (如 'bilibili_auth')
            default: 默认值
            
        Returns:
            str: 配置值
        """
        scope = ConfigService.get_current_scope()
        cache_key = ('get', scope, type_group or '', key, repr(default))
        cached, hit = _cache_get(cache_key)
        if hit:
            return cached
        
        # 1. 尝试从数据库查询
        try:
            query = SystemConfig.query.filter_by(
                key=key,
                scope=scope,
                status=1  # 只查启用的
            )
            
            if type_group:
                query = query.filter_by(type=type_group)
                
            config = query.first()
            
            if config:
                _cache_set(cache_key, config.value)
                return config.value
        except Exception as e:
            # 数据库查询失败时（如尚未初始化），回退到环境变量
            # 仅在调试模式打印，避免刷屏
            if os.environ.get('FLASK_DEBUG'):
                print(f"Warning: DB config query failed for {key}: {e}")
            pass
            
        # 2. 回退到环境变量 (尝试全大写，因为 .env 通常是大写)
        env_key = key.upper()
        # 特殊映射处理 (兼容旧的 .env 命名习惯)
        if key == 'sessdata': env_key = 'BILI_SESSDATA'
        elif key == 'bili_jct': env_key = 'BILI_BILI_JCT'
        elif key == 'buvid3': env_key = 'BILI_BUVID3'
        elif key == 'dedeuserid': env_key = 'BILI_DEDEUSERID'
        
        value = os.environ.get(env_key, default)
        _cache_set(cache_key, value)
        return value

    @staticmethod
    def set(key, value, type_group='general', description=None, scope=None):
        """
        设置/更新配置
        """
        if scope is None:
            scope = ConfigService.get_current_scope()
            
        try:
            config = SystemConfig.query.filter_by(key=key, scope=scope).first()
            
            if config:
                config.value = value
                if description: config.description = description
                config.updated_at = db.func.now()
            else:
                config = SystemConfig(
                    key=key,
                    value=value,
                    type=type_group,
                    scope=scope,
                    description=description
                )
                db.session.add(config)
            
            db.session.commit()
            _cache_clear()
            return True
        except Exception as e:
            db.session.rollback()
            print(f"Set config failed: {e}")
            return False

    @staticmethod
    def get_group(type_group):
        """
        获取某类业务下的所有配置 (返回字典)
        """
        scope = ConfigService.get_current_scope()
        cache_key = ('group', scope, type_group)
        cached, hit = _cache_get(cache_key)
        if hit:
            return dict(cached or {})

        configs = SystemConfig.query.filter_by(
            type=type_group, 
            scope=scope, 
            status=1
        ).all()
        
        values = {c.key: c.value for c in configs}
        _cache_set(cache_key, dict(values))
        return values
