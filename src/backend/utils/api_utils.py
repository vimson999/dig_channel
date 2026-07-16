# API工具函数
"""
API相关的工具函数

提供错误处理、响应格式化、验证等功能
"""

from flask import current_app, jsonify, request
from utils.security_logging import redacted_request_path
from functools import wraps
import traceback
from datetime import datetime
import json

def handle_api_error(status_code, message, details=None):
    """统一API错误处理
    
    Args:
        status_code (int): HTTP状态码
        message (str): 错误消息
        details (any): 错误详情，可选
    
    Returns:
        Response: JSON格式的错误响应
    """
    error_response = {
        'success': False,
        'error': {
            'code': status_code,
            'message': message,
            'timestamp': datetime.utcnow().isoformat()
        }
    }
    
    if details:
        error_response['error']['details'] = details
    
    # 在开发环境下添加更多调试信息
    if current_app.debug:
        error_response['error']['request_path'] = redacted_request_path()
        error_response['error']['request_method'] = request.method
        
        # 添加堆栈跟踪（仅500错误）
        if status_code >= 500:
            error_response['error']['traceback'] = traceback.format_exc()
    
    return jsonify(error_response), status_code

def validate_json(required_fields=None, optional_fields=None):
    """验证JSON请求体装饰器
    
    Args:
        required_fields (list): 必填字段列表
        optional_fields (list): 可选字段列表
    
    Returns:
        function: 装饰器函数
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # 检查Content-Type
            if not request.is_json:
                return handle_api_error(400, "请求Content-Type必须是application/json")
            
            try:
                data = request.get_json()
                if data is None:
                    return handle_api_error(400, "请求体必须是有效的JSON")
            except Exception:
                return handle_api_error(400, "JSON格式错误")
            
            # 验证必填字段
            if required_fields:
                missing_fields = []
                for field in required_fields:
                    if field not in data or data[field] is None:
                        missing_fields.append(field)
                
                if missing_fields:
                    return handle_api_error(400, f"缺少必填字段: {', '.join(missing_fields)}")
            
            # 验证字段类型（如果有定义）
            # TODO: 实现字段类型验证
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def success_response(data=None, message="操作成功", status_code=200):
    """统一成功响应格式
    
    Args:
        data (any): 响应数据
        message (str): 成功消息
        status_code (int): HTTP状态码
    
    Returns:
        Response: JSON格式的成功响应
    """
    response = {
        'success': True,
        'message': message,
        'timestamp': datetime.utcnow().isoformat()
    }
    
    if data is not None:
        response['data'] = data
    
    return jsonify(response), status_code

def paginate_response(items, page, per_page, total=None):
    """分页响应格式化
    
    Args:
        items (list): 当前页数据
        page (int): 页码
        per_page (int): 每页数量
        total (int): 总数量，可选
    
    Returns:
        dict: 包含分页信息的响应数据
    """
    if total is None:
        total = len(items)
    
    return {
        'items': items,
        'pagination': {
            'page': page,
            'per_page': per_page,
            'total': total,
            'pages': (total + per_page - 1) // per_page,
            'has_next': page * per_page < total,
            'has_prev': page > 1
        }
    }

def validate_pagination_params():
    """验证分页参数
    
    Returns:
        tuple: (page, per_page) 或 错误响应
    """
    try:
        page = int(request.args.get('page', 1))
        per_page = int(request.args.get('per_page', 10))
        
        if page < 1:
            return handle_api_error(400, "页码必须大于0")
        
        if per_page < 1 or per_page > 100:
            return handle_api_error(400, "每页数量必须在1-100之间")
        
        return page, per_page
        
    except ValueError:
        return handle_api_error(400, "分页参数必须是整数")

def validate_date_range(start_date_str, end_date_str):
    """验证日期范围参数
    
    Args:
        start_date_str (str): 开始日期字符串
        end_date_str (str): 结束日期字符串
    
    Returns:
        tuple: (start_date, end_date) 或 错误响应
    """
    from datetime import datetime, date
    
    try:
        start_date = None
        end_date = None
        
        if start_date_str:
            start_date = datetime.strptime(start_date_str, '%Y-%m-%d').date()
        
        if end_date_str:
            end_date = datetime.strptime(end_date_str, '%Y-%m-%d').date()
        
        # 验证日期逻辑
        if start_date and end_date and start_date > end_date:
            return handle_api_error(400, "开始日期不能晚于结束日期")
        
        # 验证日期不能是未来
        today = date.today()
        if start_date and start_date > today:
            return handle_api_error(400, "开始日期不能是未来日期")
        
        if end_date and end_date > today:
            return handle_api_error(400, "结束日期不能是未来日期")
        
        return start_date, end_date
        
    except ValueError:
        return handle_api_error(400, "日期格式错误，请使用YYYY-MM-DD格式")

def sanitize_string(value, max_length=None):
    """清理字符串输入
    
    Args:
        value (str): 输入字符串
        max_length (int): 最大长度限制
    
    Returns:
        str: 清理后的字符串
    """
    if not isinstance(value, str):
        return str(value)
    
    # 去除首尾空白
    value = value.strip()
    
    # 长度限制
    if max_length and len(value) > max_length:
        value = value[:max_length]
    
    return value

def validate_channel_ids(channel_ids_str):
    """验证频道ID列表参数
    
    Args:
        channel_ids_str (str): 逗号分隔的频道ID字符串
    
    Returns:
        list: 频道ID列表 或 错误响应
    """
    if not channel_ids_str:
        return []
    
    try:
        channel_ids = []
        for cid in channel_ids_str.split(','):
            cid = cid.strip()
            if cid:
                channel_id = int(cid)
                if channel_id > 0:
                    channel_ids.append(channel_id)
        
        if len(channel_ids) > 50:  # 限制最多50个
            return handle_api_error(400, "频道ID数量不能超过50个")
        
        return channel_ids
        
    except ValueError:
        return handle_api_error(400, "频道ID必须是正整数")

def rate_limit_key_func():
    """速率限制键函数
    
    Returns:
        str: 用于速率限制的键
    """
    # 基于IP地址的速率限制
    return request.remote_addr

def log_api_request():
    """记录API请求日志"""
    import logging
    
    logger = logging.getLogger('api')
    logger.info(f"{request.method} {request.url} - {request.remote_addr}")
    
    # 记录请求体（敏感信息除外）
    if request.is_json:
        try:
            data = request.get_json()
            # 过滤敏感字段
            safe_data = {k: v for k, v in data.items() if k not in ['password', 'token', 'secret']}
            logger.debug(f"Request body: {json.dumps(safe_data, ensure_ascii=False)}")
        except:
            pass

def cors_headers():
    """CORS响应头"""
    origin = request.headers.get('Origin')
    if not origin or origin not in current_app.config.get('CORS_ORIGINS', []):
        return {}
    return {
        'Access-Control-Allow-Origin': origin,
        'Vary': 'Origin',
        'Access-Control-Allow-Methods': 'GET, POST, PUT, DELETE, OPTIONS',
        'Access-Control-Allow-Headers': 'Content-Type, Authorization',
        'Access-Control-Max-Age': '3600'
    }

def cache_key_generator(*args, **kwargs):
    """缓存键生成器
    
    Returns:
        str: 缓存键
    """
    import hashlib
    
    # 基于请求URL和参数生成缓存键
    key_parts = [request.path]
    
    # 添加查询参数
    sorted_args = sorted(request.args.items())
    if sorted_args:
        key_parts.append(str(sorted_args))
    
    # 添加其他参数
    if args:
        key_parts.extend(str(arg) for arg in args)
    
    if kwargs:
        sorted_kwargs = sorted(kwargs.items())
        key_parts.append(str(sorted_kwargs))
    
    # 生成哈希
    key_string = '|'.join(key_parts)
    return hashlib.md5(key_string.encode('utf-8')).hexdigest()

class APIError(Exception):
    """API异常类"""
    
    def __init__(self, message, status_code=400, details=None):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.details = details
    
    def to_dict(self):
        return {
            'message': self.message,
            'status_code': self.status_code,
            'details': self.details
        }

def require_fields(*fields):
    """要求特定字段存在的装饰器
    
    Args:
        *fields: 必需的字段名
    
    Returns:
        function: 装饰器函数
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not request.is_json:
                return handle_api_error(400, "请求必须是JSON格式")
            
            data = request.get_json()
            missing_fields = []
            
            for field in fields:
                if field not in data or data[field] is None or data[field] == '':
                    missing_fields.append(field)
            
            if missing_fields:
                return handle_api_error(400, f"缺少必需字段: {', '.join(missing_fields)}")
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator
