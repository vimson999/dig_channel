# 用户认证API
"""
用户认证相关的RESTful API接口

提供用户注册、登录、登出、密码重置等功能
"""

from flask import Blueprint, request, jsonify, session
from flask_cors import cross_origin
from models import db, User
from utils.api_utils import handle_api_error
from functools import wraps
import jwt
import datetime
import logging
from flask import current_app

# 设置认证日志记录器
auth_logger = logging.getLogger('auth')
auth_logger.setLevel(logging.INFO)

# 创建蓝图
bp = Blueprint('auth', __name__, url_prefix='/api/v1/auth')

def require_auth(f):
    """认证装饰器"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        token = None
        
        # 从请求头获取token
        if 'Authorization' in request.headers:
            auth_header = request.headers['Authorization']
            try:
                token = auth_header.split(" ")[1]  # Bearer <token>
            except IndexError:
                return handle_api_error(401, "无效的认证头格式")
        
        # 从session获取token（备选方案）
        if not token and 'access_token' in session:
            token = session['access_token']
        
        if not token:
            return handle_api_error(401, "缺少认证令牌")
        
        try:
            # 解码token
            data = jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=['HS256'])
            current_user = User.query.get(data['user_id'])
            
            if not current_user or not current_user.is_active:
                return handle_api_error(401, "用户不存在或已被禁用")
            
            # 将当前用户信息传递给视图函数
            return f(current_user, *args, **kwargs)
            
        except jwt.ExpiredSignatureError:
            return handle_api_error(401, "令牌已过期")
        except jwt.InvalidTokenError:
            return handle_api_error(401, "无效的令牌")
        except Exception as e:
            return handle_api_error(401, f"认证失败: {str(e)}")
    
    return decorated_function

def generate_token(user_id, expires_in=24*60*60):
    """生成JWT令牌"""
    payload = {
        'user_id': user_id,
        'exp': datetime.datetime.utcnow() + datetime.timedelta(seconds=expires_in),
        'iat': datetime.datetime.utcnow()
    }
    return jwt.encode(payload, current_app.config['SECRET_KEY'], algorithm='HS256')

@bp.route('/register', methods=['POST'])
@cross_origin()
def register():
    """用户注册
    
    Body Parameters:
        username (str): 用户名，3-20字符
        email (str): 邮箱地址
        password (str): 密码，至少6位
        display_name (str, optional): 显示名称
    
    Returns:
        JSON: 注册结果
    """
    try:
        data = request.get_json()
        
        if not data:
            return handle_api_error(400, "请求体不能为空")
        
        # 验证必填字段
        required_fields = ['username', 'email', 'password']
        for field in required_fields:
            if not data.get(field):
                return handle_api_error(400, f"缺少必填字段: {field}")
        
        username = data['username'].strip()
        email = data['email'].strip()
        password = data['password']
        display_name = data.get('display_name', '').strip() or username
        
        # 验证字段格式
        if len(username) < 3 or len(username) > 20:
            return handle_api_error(400, "用户名长度必须在3-20字符之间")
        
        if len(password) < 6:
            return handle_api_error(400, "密码长度至少6位")
        
        # 简单的邮箱格式验证
        if '@' not in email or '.' not in email:
            return handle_api_error(400, "邮箱格式不正确")
        
        # 创建用户
        try:
            user = User.create_user(
                username=username,
                email=email,
                password=password,
                display_name=display_name
            )
            
            # 生成令牌
            token = generate_token(user.id)
            
            # 保存到session（可选）
            session['access_token'] = token
            session['user_id'] = user.id
            
            return jsonify({
                'success': True,
                'message': '注册成功',
                'data': {
                    'user': user.to_safe_dict(),
                    'token': token,
                    'token_type': 'Bearer'
                }
            }), 201
            
        except ValueError as e:
            return handle_api_error(400, str(e))
        
    except Exception as e:
        return handle_api_error(500, f"注册失败: {str(e)}")

@bp.route('/login', methods=['POST'])
@cross_origin()
def login():
    """用户登录
    
    Body Parameters:
        username_or_email (str): 用户名或邮箱
        password (str): 密码
        remember_me (bool, optional): 是否记住登录状态
    
    Returns:
        JSON: 登录结果
    """
    client_ip = request.environ.get('HTTP_X_FORWARDED_FOR', request.environ.get('REMOTE_ADDR', 'unknown'))
    auth_logger.info(f"[🔑登录请求] IP: {client_ip}, Method: {request.method}")
    try:
        data = request.get_json()
        
        if not data:
            return handle_api_error(400, "请求体不能为空")
        
        # 支持两种参数名格式
        username_or_email = data.get('username_or_email', '').strip() or data.get('username', '').strip()
        password = data.get('password', '')
        remember_me = data.get('remember_me', False)
        
        if not username_or_email or not password:
            return handle_api_error(400, "用户名/邮箱和密码不能为空")
        
        # 用户认证
        user = User.authenticate(username_or_email, password)
        
        if not user:
            return handle_api_error(401, "用户名/邮箱或密码错误")
        
        # 生成令牌
        expires_in = 7*24*60*60 if remember_me else 24*60*60  # 记住登录7天，否则1天
        token = generate_token(user.id, expires_in)
        
        # 保存到session
        session['access_token'] = token
        session['user_id'] = user.id
        session.permanent = remember_me
        
        response_data = {
            'success': True,
            'message': '登录成功',
            'access_token': token,
            'token_type': 'Bearer',
            'expires_in': expires_in,
            'user': user.to_safe_dict()
        }
        
        auth_logger.info(f"[✅登录成功] 用户: {user.username} (ID: {user.id}), Token长度: {len(token)}, 响应键: {list(response_data.keys())}")
        
        return jsonify(response_data)
        
    except Exception as e:
        auth_logger.error(f"[❌登录异常] IP: {client_ip}, 错误: {str(e)}")
        return handle_api_error(500, f"登录失败: {str(e)}")

@bp.route('/logout', methods=['POST'])
@require_auth
def logout(current_user):
    """用户登出
    
    Returns:
        JSON: 登出结果
    """
    try:
        # 清除session
        session.clear()
        
        return jsonify({
            'success': True,
            'message': '登出成功'
        })
        
    except Exception as e:
        return handle_api_error(500, f"登出失败: {str(e)}")

@bp.route('/me', methods=['GET'])
@require_auth
def get_current_user(current_user):
    """获取当前用户信息
    
    Returns:
        JSON: 用户信息
    """
    try:
        return jsonify({
            'success': True,
            'data': {
                'user': current_user.to_safe_dict()
            }
        })
        
    except Exception as e:
        return handle_api_error(500, f"获取用户信息失败: {str(e)}")

@bp.route('/me', methods=['PUT'])
@require_auth
def update_current_user(current_user):
    """更新当前用户信息
    
    Body Parameters:
        display_name (str, optional): 显示名称
        timezone (str, optional): 时区
        language (str, optional): 语言
    
    Returns:
        JSON: 更新结果
    """
    try:
        data = request.get_json()
        
        if not data:
            return handle_api_error(400, "请求体不能为空")
        
        # 更新允许的字段
        if 'display_name' in data:
            current_user.display_name = data['display_name'].strip()
        
        if 'timezone' in data:
            current_user.timezone = data['timezone']
        
        if 'language' in data:
            current_user.language = data['language']
        
        current_user.updated_at = datetime.datetime.utcnow()
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '用户信息更新成功',
            'data': {
                'user': current_user.to_safe_dict()
            }
        })
        
    except Exception as e:
        db.session.rollback()
        return handle_api_error(500, f"更新用户信息失败: {str(e)}")

@bp.route('/change-password', methods=['POST'])
@require_auth
def change_password(current_user):
    """修改密码
    
    Body Parameters:
        old_password (str): 原密码
        new_password (str): 新密码
    
    Returns:
        JSON: 修改结果
    """
    try:
        data = request.get_json()
        
        if not data:
            return handle_api_error(400, "请求体不能为空")
        
        old_password = data.get('old_password', '')
        new_password = data.get('new_password', '')
        
        if not old_password or not new_password:
            return handle_api_error(400, "原密码和新密码不能为空")
        
        if len(new_password) < 6:
            return handle_api_error(400, "新密码长度至少6位")
        
        # 验证原密码
        if not current_user.check_password(old_password):
            return handle_api_error(400, "原密码错误")
        
        # 设置新密码
        current_user.set_password(new_password)
        current_user.updated_at = datetime.datetime.utcnow()
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '密码修改成功'
        })
        
    except Exception as e:
        db.session.rollback()
        return handle_api_error(500, f"修改密码失败: {str(e)}")

@bp.route('/verify', methods=['GET', 'POST'])
@cross_origin()
@require_auth
def verify(current_user):
    """验证当前令牌
    
    Headers:
        Authorization: Bearer <token>
    
    Returns:
        JSON: 验证结果
    """
    client_ip = request.environ.get('HTTP_X_FORWARDED_FOR', request.environ.get('REMOTE_ADDR', 'unknown'))
    auth_logger.info(f"[🔐Token验证] IP: {client_ip}, 用户: {current_user.username} (ID: {current_user.id})")
    
    try:
        response_data = {
            'success': True,
            'message': '令牌有效',
            'data': {
                'user': current_user.to_safe_dict()
            }
        }
        
        auth_logger.info(f"[✅验证成功] 用户: {current_user.username} (ID: {current_user.id})")
        return jsonify(response_data)
    except Exception as e:
        auth_logger.error(f"[❌验证异常] IP: {client_ip}, 用户: {current_user.username}, 错误: {str(e)}")
        return handle_api_error(500, f"验证失败: {str(e)}")

@bp.route('/verify-token', methods=['POST'])
def verify_token():
    """验证令牌有效性
    
    Body Parameters:
        token (str): 需要验证的令牌
    
    Returns:
        JSON: 验证结果
    """
    try:
        data = request.get_json()
        token = data.get('token') if data else None
        
        if not token:
            return handle_api_error(400, "缺少令牌")
        
        try:
            # 解码token
            payload = jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=['HS256'])
            user = User.query.get(payload['user_id'])
            
            if not user or not user.is_active:
                return handle_api_error(401, "用户不存在或已被禁用")
            
            return jsonify({
                'success': True,
                'message': '令牌有效',
                'data': {
                    'user': user.to_safe_dict(),
                    'expires_at': datetime.datetime.fromtimestamp(payload['exp']).isoformat()
                }
            })
            
        except jwt.ExpiredSignatureError:
            return handle_api_error(401, "令牌已过期")
        except jwt.InvalidTokenError:
            return handle_api_error(401, "无效的令牌")
        
    except Exception as e:
        return handle_api_error(500, f"验证令牌失败: {str(e)}")

# 错误处理
@bp.errorhandler(404)
def not_found(error):
    return handle_api_error(404, "认证API端点不存在")

@bp.errorhandler(405)
def method_not_allowed(error):
    return handle_api_error(405, "请求方法不被允许")

@bp.errorhandler(400)
def bad_request(error):
    return handle_api_error(400, "请求参数错误")