# 认证工具模块
"""
认证相关的工具函数和装饰器

提供统一的认证方法，方便在整个应用中使用
"""

from functools import wraps
from flask import request, session, current_app
import jwt
from models import User
from utils.api_utils import handle_api_error

def get_current_user():
    """获取当前登录用户
    
    Returns:
        User: 当前用户实例，如果未登录返回None
    """
    # 尝试从JWT token获取用户
    user = get_user_from_token()
    if user:
        return user
    
    # 尝试从session获取用户
    return get_user_from_session()

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
        data = jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=['HS256'])
        user = User.query.get(data['user_id'])
        
        if user and user.is_active:
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
    
    user = User.query.get(user_id)
    if user and user.is_active:
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
        
        # 开发环境：如果没有用户，使用admin用户或创建默认用户
        if not current_user and current_app.config.get('DEBUG', False):
            try:
                # 优先使用已有的admin用户(通常有数据)
                admin_user = User.query.filter_by(username='admin').first()
                if admin_user and admin_user.is_active:
                    print(f"🔧 [开发模式] 使用admin用户: {admin_user.username} (ID: {admin_user.id})")
                    current_user = admin_user
                else:
                    # 如果没有admin用户，则查找或创建test_user
                    test_user = User.query.filter_by(username='test_user').first()
                    if not test_user:
                        from models import db
                        test_user = User(
                            username='test_user',
                            email='test@example.com',
                            display_name='测试用户',
                            is_admin=True
                        )
                        db.session.add(test_user)
                        try:
                            db.session.commit()
                            print(f"🔧 [开发模式] 创建默认测试用户: {test_user.username}")
                        except Exception as e:
                            db.session.rollback()
                            print(f"❌ 创建测试用户失败: {e}")
                            # 尝试再次查找
                            test_user = User.query.filter_by(username='test_user').first()
                    
                    if test_user:
                        print(f"🔧 [开发模式] 使用测试用户: {test_user.username} (ID: {test_user.id})")
                        current_user = test_user
            except Exception as db_error:
                print(f"🔧 [开发模式] 数据库连接失败，创建临时用户对象: {db_error}")
                # 数据库连接失败时，创建一个临时用户对象
                class TempUser:
                    def __init__(self):
                        self.id = 1
                        self.username = 'temp_user'
                        self.email = 'temp@example.com'
                        self.display_name = '临时用户'
                        self.is_admin = True
                current_user = TempUser()
        
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
    
    payload = {
        'user_id': user_id,
        'exp': datetime.datetime.utcnow() + datetime.timedelta(seconds=expires_in),
        'iat': datetime.datetime.utcnow()
    }
    return jwt.encode(payload, current_app.config['SECRET_KEY'], algorithm='HS256')

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
    session['access_token'] = token
    session['user_id'] = user.id
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