# 用户认证API
"""
用户认证相关的RESTful API接口

提供用户注册、登录、登出、密码重置等功能
"""

from flask import Blueprint, request, jsonify, session
from models import db, User
from utils.api_utils import handle_api_error
from utils.auth_utils import (
    auth_version_for_user,
    generate_token,
    get_current_user as resolve_current_user,
    require_login as require_auth,
)
from utils.security_logging import account_fingerprint, log_security_event
from extensions import limiter
import jwt
import datetime
import time
from flask import current_app

# 创建蓝图
bp = Blueprint('auth', __name__, url_prefix='/api/v1/auth')


def _login_account_identifier():
    data = request.get_json(silent=True) or {}
    account = data.get('username_or_email') or data.get('username') or ''
    return f"account:{account_fingerprint(account)}"


def _count_auth_failure(response):
    return response.status_code == 401


def _portal_access_is_valid():
    if str(current_app.config.get('AUTH_MODE') or '').lower() == 'local':
        return True
    verified_at = session.get('login_portal_verified_at')
    try:
        age = time.time() - float(verified_at)
    except (TypeError, ValueError):
        return False
    return 0 <= age <= int(current_app.config.get('LOGIN_PORTAL_TTL_SECONDS', 600))

@bp.route('/register', methods=['POST'])
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
    if not current_app.config.get('PUBLIC_REGISTRATION_ENABLED', False):
        log_security_event('account.register', 'denied', reason='registration_disabled')
        return handle_api_error(404, "接口不存在")

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
        
        minimum = int(current_app.config.get('PASSWORD_MIN_LENGTH', 14))
        if len(password) < minimum:
            return handle_api_error(400, f"密码长度至少{minimum}位")
        
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
            session['user_id'] = user.id
            session['auth_version'] = auth_version_for_user(user)
            log_security_event('account.register', 'success', user=user)
            
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
        log_security_event('account.register', 'error', reason=type(e).__name__)
        return handle_api_error(500, "注册失败，请稍后重试")

@bp.route('/login', methods=['POST'])
@limiter.limit(
    lambda: current_app.config.get('LOGIN_IP_RATE_LIMIT', '30 per minute;300 per day')
)
@limiter.limit(
    lambda: current_app.config.get('LOGIN_ACCOUNT_RATE_LIMIT', '5 per 15 minutes'),
    key_func=_login_account_identifier,
    deduct_when=_count_auth_failure,
)
def login():
    """用户登录
    
    Body Parameters:
        username_or_email (str): 用户名或邮箱
        password (str): 密码
        remember_me (bool, optional): 是否记住登录状态
    
    Returns:
        JSON: 登录结果
    """
    if not _portal_access_is_valid():
        log_security_event('auth.login', 'denied', reason='portal_required')
        return handle_api_error(404, "接口不存在")

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
        if len(username_or_email) > 254 or len(password) > 1024:
            log_security_event('auth.login', 'failure', account=username_or_email, reason='input_too_long')
            return handle_api_error(400, "登录信息格式不正确")
        
        # 用户认证
        user = User.authenticate(username_or_email, password)
        
        if not user:
            log_security_event('auth.login', 'failure', account=username_or_email, reason='invalid_credentials')
            return handle_api_error(401, "用户名/邮箱或密码错误")
        
        # 生成令牌
        expires_in = int(current_app.config.get('JWT_ACCESS_TOKEN_EXPIRES', 900))
        token = generate_token(user.id, expires_in)
        
        # 清除预认证状态并建立新的已认证会话，降低会话固定风险。
        session.clear()
        session['user_id'] = user.id
        session['auth_version'] = auth_version_for_user(user)
        session.permanent = remember_me
        
        response_data = {
            'success': True,
            'message': '登录成功',
            'access_token': token,
            'token_type': 'Bearer',
            'expires_in': expires_in,
            'user': user.to_safe_dict()
        }
        
        log_security_event('auth.login', 'success', user=user)
        
        return jsonify(response_data)
        
    except Exception as e:
        log_security_event('auth.login', 'error', reason=type(e).__name__)
        return handle_api_error(500, "登录失败，请稍后重试")


@bp.route('/session-check', methods=['GET'])
def session_check():
    """Minimal endpoint used by Nginx auth_request."""
    current_user = resolve_current_user()
    if not current_user:
        return '', 401
    if current_app.config.get('AUTH_REQUIRE_ADMIN', True) and not current_user.is_admin:
        return '', 403
    return '', 204

@bp.route('/logout', methods=['POST'])
@require_auth
def logout(current_user):
    """用户登出
    
    Returns:
        JSON: 登出结果
    """
    try:
        # 清除session
        log_security_event('auth.logout', 'success', user=current_user)
        session.clear()
        
        return jsonify({
            'success': True,
            'message': '登出成功'
        })
        
    except Exception as e:
        log_security_event('auth.logout', 'error', reason=type(e).__name__)
        return handle_api_error(500, "登出失败，请稍后重试")

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
        return handle_api_error(500, "获取用户信息失败")

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
        response_user = current_user.to_safe_dict()
        db.session.commit()
        
        return jsonify({
            'success': True,
            'message': '用户信息更新成功',
            'data': {
                'user': response_user
            }
        })
        
    except Exception as e:
        db.session.rollback()
        return handle_api_error(500, "更新用户信息失败")

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
        
        minimum = int(current_app.config.get('PASSWORD_MIN_LENGTH', 14))
        if len(new_password) < minimum:
            return handle_api_error(400, f"新密码长度至少{minimum}位")
        
        # 验证原密码
        if not current_user.check_password(old_password):
            return handle_api_error(400, "原密码错误")
        
        # 设置新密码
        user_id = current_user.id
        current_user.set_password(new_password)
        current_user.revoke_sessions()
        new_auth_version = auth_version_for_user(current_user)
        current_user.updated_at = datetime.datetime.utcnow()
        db.session.flush()
        log_security_event('auth.password_change', 'success', user=current_user)
        db.session.commit()
        session['user_id'] = user_id
        session['auth_version'] = new_auth_version
        session.pop('access_token', None)
        
        return jsonify({
            'success': True,
            'message': '密码修改成功'
        })
        
    except Exception as e:
        db.session.rollback()
        log_security_event('auth.password_change', 'error', reason=type(e).__name__)
        return handle_api_error(500, "修改密码失败")

@bp.route('/verify', methods=['GET', 'POST'])
@require_auth
def verify(current_user):
    """验证当前令牌
    
    Headers:
        Authorization: Bearer <token>
    
    Returns:
        JSON: 验证结果
    """
    try:
        response_data = {
            'success': True,
            'message': '令牌有效',
            'data': {
                'user': current_user.to_safe_dict()
            }
        }
        
        return jsonify(response_data)
    except Exception as e:
        log_security_event('auth.verify', 'error', user=current_user, reason=type(e).__name__)
        return handle_api_error(500, "验证失败")

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
            payload = jwt.decode(token, current_app.config['JWT_SECRET_KEY'], algorithms=['HS256'])
            if current_app.config.get('AUTH_VERSION_ENABLED', True):
                user = User.query.get(payload['user_id'])
            else:
                from sqlalchemy.orm import load_only
                user = User.query.options(load_only(
                    User.id, User.username, User.email, User.display_name, User.avatar_url,
                    User.is_active, User.is_verified, User.is_admin, User.timezone, User.language,
                    User.created_at, User.updated_at, User.last_login_at,
                )).filter(User.id == payload['user_id']).first()
            
            token_version = int(payload.get('auth_version') or 1)
            if not user or not user.is_active or auth_version_for_user(user) != token_version:
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
        log_security_event('auth.verify_token', 'error', reason=type(e).__name__)
        return handle_api_error(500, "验证令牌失败")

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
