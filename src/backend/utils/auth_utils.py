# 认证工具模块
"""
认证相关的工具函数和装饰器

提供统一的认证方法，方便在整个应用中使用
"""

import os
import time
import ipaddress
import logging
from functools import wraps
from threading import RLock

from flask import request, session, current_app, g, has_request_context
import jwt
from sqlalchemy.orm import load_only
from models import User
from utils.api_utils import handle_api_error


LIGHT_USER_COLUMNS = (
    User.id,
    User.username,
    User.email,
    User.display_name,
    User.avatar_url,
    User.is_active,
    User.is_verified,
    User.is_admin,
    User.timezone,
    User.language,
    User.created_at,
    User.updated_at,
    User.last_login_at,
)

logger = logging.getLogger('security')


def _float_env(name, default):
    try:
        return float(os.environ.get(name, default))
    except (TypeError, ValueError):
        return float(default)


_DEV_FALLBACK_USER_CACHE_TTL = max(0.0, _float_env("DEV_FALLBACK_USER_CACHE_TTL", 300))
_DEV_FALLBACK_USER_CACHE = {"expires_at": 0.0, "user": None}
_DEV_FALLBACK_USER_CACHE_LOCK = RLock()


class DevFallbackUser:
    def __init__(self, user):
        self.id = user.id
        self.username = user.username
        self.email = user.email
        self.display_name = user.display_name
        self.avatar_url = user.avatar_url
        self.is_active = user.is_active
        self.is_verified = user.is_verified
        self.is_admin = user.is_admin
        self.timezone = user.timezone
        self.language = user.language
        self.created_at = user.created_at
        self.updated_at = user.updated_at
        self.last_login_at = user.last_login_at
        self.auth_version = auth_version_for_user(user)

    def to_dict(self, include_sensitive=False):
        return {
            'id': self.id,
            'username': self.username,
            'email': self.email if include_sensitive else None,
            'display_name': self.display_name,
            'avatar_url': self.avatar_url,
            'is_active': self.is_active,
            'is_verified': self.is_verified,
            'is_admin': self.is_admin,
            'timezone': self.timezone,
            'language': self.language,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'last_login_at': self.last_login_at.isoformat() if self.last_login_at else None,
            'auth_version': self.auth_version if include_sensitive else None,
        }

    def to_safe_dict(self):
        return self.to_dict(include_sensitive=False)

    def has_permission(self, _permission):
        return bool(self.is_admin)


def _light_user_query():
    columns = LIGHT_USER_COLUMNS
    if current_app.config.get('AUTH_VERSION_ENABLED', True):
        columns = (*columns, User.auth_version)
    return User.query.options(load_only(*columns))


def auth_version_for_user(user):
    if not current_app.config.get('AUTH_VERSION_ENABLED', True):
        return 1
    return int(user.auth_version or 1)


def _get_light_user_by_id(user_id):
    return _light_user_query().filter(User.id == user_id).first()


def _cache_dev_fallback_user(user):
    fallback_user = DevFallbackUser(user)
    if _DEV_FALLBACK_USER_CACHE_TTL > 0:
        with _DEV_FALLBACK_USER_CACHE_LOCK:
            _DEV_FALLBACK_USER_CACHE["user"] = fallback_user
            _DEV_FALLBACK_USER_CACHE["expires_at"] = time.time() + _DEV_FALLBACK_USER_CACHE_TTL
    return fallback_user


def _get_cached_dev_fallback_user():
    if _DEV_FALLBACK_USER_CACHE_TTL <= 0:
        return None
    with _DEV_FALLBACK_USER_CACHE_LOCK:
        cached = _DEV_FALLBACK_USER_CACHE.get("user")
        if cached and _DEV_FALLBACK_USER_CACHE.get("expires_at", 0.0) > time.time():
            return cached
    return None


def _is_direct_loopback_request():
    """Local bypass is valid only for a direct loopback connection."""
    if request.headers.get('X-Forwarded-For') or request.headers.get('X-Real-IP'):
        return False
    try:
        return ipaddress.ip_address(request.remote_addr or '').is_loopback
    except ValueError:
        return False


def _get_local_bypass_user():
    if not current_app.config.get('LOCAL_AUTH_BYPASS', False):
        return None
    if str(current_app.config.get('AUTH_MODE') or '').lower() != 'local':
        return None
    if not _is_direct_loopback_request():
        logger.warning('local_auth_bypass_denied remote_addr=%s', request.remote_addr)
        return None

    with _DEV_FALLBACK_USER_CACHE_LOCK:
        cached_user = _get_cached_dev_fallback_user()
        if cached_user:
            return cached_user

        admin_user = _light_user_query().filter_by(is_admin=True, is_active=True).order_by(User.id.asc()).first()
        if not admin_user:
            logger.error('local_auth_bypass_unavailable reason=no_active_admin')
            return None
        return _cache_dev_fallback_user(admin_user)


def get_current_user():
    """获取当前登录用户
    
    Returns:
        User: 当前用户实例，如果未登录返回None
    """
    # The project-wide before_request guard resolves authentication first and
    # stores the result on ``g``. Route-level decorators run afterwards; reuse
    # that request-scoped decision instead of issuing the same user lookup a
    # second time. Do not cache a miss here because login/session mutation can
    # still occur later in the same public request.
    if has_request_context():
        cached_user = getattr(g, "current_user", None)
        if cached_user is not None:
            return cached_user

    # 尝试从JWT token获取用户
    user = get_user_from_token()
    if user:
        return user
    
    # 尝试从session获取用户
    user = get_user_from_session()
    if user:
        return user

    return _get_local_bypass_user()

def get_user_from_token():
    """从JWT token获取用户
    
    Returns:
        User: 用户实例，如果token无效返回None
    """
    token = None
    
    # 从请求头获取token
    if 'Authorization' in request.headers:
        auth_header = request.headers['Authorization']
        try:
            token = auth_header.split(" ")[1]  # Bearer <token>
        except IndexError:
            return None
    
    if not token:
        return None
    
    try:
        # 解码token
        data = jwt.decode(token, current_app.config['JWT_SECRET_KEY'], algorithms=['HS256'])
        user = _get_light_user_by_id(data['user_id'])
        
        token_version = int(data.get('auth_version') or 1)
        if user and user.is_active and auth_version_for_user(user) == token_version:
            return user
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError):
        pass
    
    return None

def get_user_from_session():
    """从session获取用户
    
    Returns:
        User: 用户实例，如果session无效返回None
    """
    user_id = session.get('user_id')
    if not user_id:
        return None
    
    user = _get_light_user_by_id(user_id)
    session_version = int(session.get('auth_version') or 1)
    if user and user.is_active and auth_version_for_user(user) == session_version:
        return user
    
    # 清除无效session
    session.clear()
    return None

def require_login(f):
    """登录验证装饰器
    
    用于保护需要登录的API端点
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        current_user = get_current_user()
        
        if not current_user:
            return handle_api_error(401, "请先登录")
        
        # 将当前用户作为第一个参数传递给被装饰的函数
        return f(current_user, *args, **kwargs)
    
    return decorated_function

def require_admin(f):
    """管理员权限验证装饰器
    
    用于保护需要管理员权限的API端点
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        current_user = get_current_user()
        
        if not current_user:
            return handle_api_error(401, "请先登录")
        
        if not current_user.is_admin:
            return handle_api_error(403, "需要管理员权限")
        
        return f(current_user, *args, **kwargs)
    
    return decorated_function

def check_resource_permission(resource_user_id):
    """检查资源访问权限
    
    Args:
        resource_user_id (int): 资源所属用户ID
        
    Returns:
        tuple: (是否有权限, 当前用户)
    """
    current_user = get_current_user()
    
    if not current_user:
        return False, None
    
    # 管理员可以访问所有资源
    if current_user.is_admin:
        return True, current_user
    
    # 用户只能访问自己的资源
    if current_user.id == resource_user_id:
        return True, current_user
    
    return False, current_user

def require_resource_permission(get_resource_user_id):
    """资源权限验证装饰器
    
    Args:
        get_resource_user_id (callable): 函数，用于从请求参数中获取资源的用户ID
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            try:
                resource_user_id = get_resource_user_id(*args, **kwargs)
            except Exception:
                return handle_api_error(400, "无效的资源标识")
            
            has_permission, current_user = check_resource_permission(resource_user_id)
            
            if not has_permission:
                if not current_user:
                    return handle_api_error(401, "请先登录")
                else:
                    return handle_api_error(403, "无权访问此资源")
            
            return f(current_user, *args, **kwargs)
        
        return decorated_function
    return decorator

def generate_token(user_id, expires_in=24*60*60):
    """生成JWT令牌
    
    Args:
        user_id (int): 用户ID
        expires_in (int): 过期时间（秒）
        
    Returns:
        str: JWT令牌
    """
    import datetime
    
    user = _get_light_user_by_id(user_id)
    auth_version = auth_version_for_user(user) if user else 1
    payload = {
        'user_id': user_id,
        'auth_version': auth_version,
        'exp': datetime.datetime.utcnow() + datetime.timedelta(seconds=expires_in),
        'iat': datetime.datetime.utcnow()
    }
    return jwt.encode(payload, current_app.config['JWT_SECRET_KEY'], algorithm='HS256')

def is_authenticated():
    """检查用户是否已认证
    
    Returns:
        bool: 是否已认证
    """
    return get_current_user() is not None

def login_user(user, remember_me=False):
    """登录用户
    
    Args:
        user (User): 用户实例
        remember_me (bool): 是否记住登录状态
        
    Returns:
        str: JWT令牌
    """
    # 生成令牌
    expires_in = 7*24*60*60 if remember_me else 24*60*60  # 记住登录7天，否则1天
    token = generate_token(user.id, expires_in)
    
    # 保存到session
    session['user_id'] = user.id
    session['auth_version'] = auth_version_for_user(user)
    session.permanent = remember_me
    
    # 更新最后登录时间
    user.update_last_login()
    
    return token

def logout_user():
    """登出用户"""
    session.clear()

def get_user_channels(user_id=None, active_only=True):
    """获取用户频道列表
    
    Args:
        user_id (int, optional): 用户ID，默认为当前用户
        active_only (bool): 是否只返回活跃频道
        
    Returns:
        list: 频道列表
    """
    if user_id is None:
        current_user = get_current_user()
        if not current_user:
            return []
        user_id = current_user.id
    
    from models import Channel
    return Channel.get_user_channels(user_id, active_only)

def get_user_videos(user_id=None, channel_id=None, active_only=True, limit=None, offset=None):
    """获取用户视频列表
    
    Args:
        user_id (int, optional): 用户ID，默认为当前用户
        channel_id (int, optional): 频道ID
        active_only (bool): 是否只返回活跃视频
        limit (int, optional): 限制数量
        offset (int, optional): 偏移量
        
    Returns:
        list: 视频列表
    """
    if user_id is None:
        current_user = get_current_user()
        if not current_user:
            return []
        user_id = current_user.id
    
    from models import Video
    return Video.get_user_videos(user_id, channel_id, active_only, limit, offset)
