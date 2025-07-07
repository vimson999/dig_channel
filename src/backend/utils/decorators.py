# API装饰器
"""
API相关的装饰器函数

提供请求验证、缓存、限流等功能
"""

from functools import wraps
from flask import request, jsonify, g
from utils.api_utils import handle_api_error
import time
import json
from datetime import datetime, timedelta

def validate_request(required_fields=None, optional_fields=None):
    """验证请求数据装饰器
    
    Args:
        required_fields (list): 必填字段列表
        optional_fields (list): 可选字段列表
    
    Returns:
        function: 装饰器函数
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # 对于GET请求，不验证JSON
            if request.method == 'GET':
                return f(*args, **kwargs)
            
            # 验证Content-Type
            if request.method in ['POST', 'PUT', 'PATCH']:
                if not request.is_json:
                    return handle_api_error(400, "请求Content-Type必须是application/json")
                
                try:
                    data = request.get_json()
                    if data is None:
                        data = {}
                except Exception:
                    return handle_api_error(400, "JSON格式错误")
                
                # 验证必填字段
                if required_fields:
                    missing_fields = []
                    for field in required_fields:
                        if field not in data or data[field] is None or str(data[field]).strip() == '':
                            missing_fields.append(field)
                    
                    if missing_fields:
                        return handle_api_error(400, f"缺少必填字段: {', '.join(missing_fields)}")
                
                # 验证字段长度
                for field, value in data.items():
                    if isinstance(value, str) and len(value) > 1000:  # 限制字符串字段长度
                        return handle_api_error(400, f"字段 {field} 长度不能超过1000字符")
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def cache_response(timeout=300, key_func=None):
    """缓存响应装饰器
    
    Args:
        timeout (int): 缓存超时时间（秒）
        key_func (function): 缓存键生成函数
    
    Returns:
        function: 装饰器函数
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # 生成缓存键
            if key_func:
                cache_key = key_func(*args, **kwargs)
            else:
                cache_key = f"{request.path}?{request.query_string.decode()}"
            
            # 检查缓存
            # TODO: 实现Redis缓存
            # cached_response = redis_client.get(cache_key)
            # if cached_response:
            #     return json.loads(cached_response)
            
            # 执行函数
            response = f(*args, **kwargs)
            
            # 缓存响应
            # TODO: 实现Redis缓存
            # redis_client.setex(cache_key, timeout, json.dumps(response))
            
            return response
        return decorated_function
    return decorator

def rate_limit(max_requests=100, window=3600, key_func=None):
    """速率限制装饰器
    
    Args:
        max_requests (int): 最大请求数
        window (int): 时间窗口（秒）
        key_func (function): 限流键生成函数
    
    Returns:
        function: 装饰器函数
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # 生成限流键
            if key_func:
                limit_key = key_func()
            else:
                limit_key = request.remote_addr
            
            # 检查速率限制
            # TODO: 实现Redis速率限制
            # current_requests = redis_client.get(f"rate_limit:{limit_key}")
            # if current_requests and int(current_requests) >= max_requests:
            #     return handle_api_error(429, "请求频率过高，请稍后再试")
            
            # 记录请求
            # redis_client.incr(f"rate_limit:{limit_key}")
            # redis_client.expire(f"rate_limit:{limit_key}", window)
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def log_request():
    """记录请求日志装饰器"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            start_time = time.time()
            
            # 记录请求开始
            print(f"[{datetime.now().isoformat()}] {request.method} {request.url} - {request.remote_addr}")
            
            try:
                # 执行函数
                response = f(*args, **kwargs)
                
                # 记录成功响应
                duration = time.time() - start_time
                status_code = getattr(response, 'status_code', 200)
                print(f"[{datetime.now().isoformat()}] Response {status_code} - {duration:.3f}s")
                
                return response
                
            except Exception as e:
                # 记录错误
                duration = time.time() - start_time
                print(f"[{datetime.now().isoformat()}] Error: {str(e)} - {duration:.3f}s")
                raise
                
        return decorated_function
    return decorator

def require_api_key():
    """需要API密钥的装饰器"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # 获取API密钥
            api_key = request.headers.get('X-API-Key') or request.args.get('api_key')
            
            if not api_key:
                return handle_api_error(401, "缺少API密钥")
            
            # 验证API密钥
            # TODO: 实现API密钥验证逻辑
            valid_keys = ['dev-api-key', 'test-api-key']  # 临时用于测试
            if api_key not in valid_keys:
                return handle_api_error(403, "无效的API密钥")
            
            # 将API密钥信息添加到请求上下文
            g.api_key = api_key
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def handle_errors():
    """统一错误处理装饰器"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            try:
                return f(*args, **kwargs)
            except ValueError as e:
                return handle_api_error(400, f"参数错误: {str(e)}")
            except KeyError as e:
                return handle_api_error(400, f"缺少必要参数: {str(e)}")
            except Exception as e:
                # 记录详细错误信息
                import traceback
                error_details = traceback.format_exc()
                print(f"API错误: {error_details}")
                
                return handle_api_error(500, f"服务器内部错误: {str(e)}")
        return decorated_function
    return decorator

def validate_json_schema(schema):
    """JSON模式验证装饰器
    
    Args:
        schema (dict): JSON模式定义
    
    Returns:
        function: 装饰器函数
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if not request.is_json:
                return handle_api_error(400, "请求必须是JSON格式")
            
            try:
                data = request.get_json()
                # TODO: 实现JSON模式验证
                # 这里可以使用jsonschema库进行验证
                # validate(data, schema)
            except Exception as e:
                return handle_api_error(400, f"JSON验证失败: {str(e)}")
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def cors_enabled():
    """启用CORS的装饰器"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # 处理预检请求
            if request.method == 'OPTIONS':
                response = jsonify({'message': 'CORS preflight'})
                response.headers.add('Access-Control-Allow-Origin', '*')
                response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
                response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
                return response
            
            # 执行原函数
            response = f(*args, **kwargs)
            
            # 添加CORS头
            if hasattr(response, 'headers'):
                response.headers.add('Access-Control-Allow-Origin', '*')
                response.headers.add('Access-Control-Allow-Headers', 'Content-Type,Authorization')
                response.headers.add('Access-Control-Allow-Methods', 'GET,PUT,POST,DELETE,OPTIONS')
            
            return response
        return decorated_function
    return decorator

def timing():
    """API响应时间统计装饰器"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            start_time = time.time()
            
            response = f(*args, **kwargs)
            
            duration = time.time() - start_time
            
            # 添加响应时间头
            if hasattr(response, 'headers'):
                response.headers.add('X-Response-Time', f"{duration:.3f}s")
            
            # 记录慢查询
            if duration > 1.0:  # 超过1秒
                print(f"慢查询警告: {request.method} {request.path} - {duration:.3f}s")
            
            return response
        return decorated_function
    return decorator

def require_content_type(content_type='application/json'):
    """要求特定Content-Type的装饰器
    
    Args:
        content_type (str): 要求的Content-Type
    
    Returns:
        function: 装饰器函数
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if request.method in ['POST', 'PUT', 'PATCH']:
                if request.content_type != content_type:
                    return handle_api_error(400, f"Content-Type必须是{content_type}")
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator

def paginate(default_per_page=10, max_per_page=100):
    """分页装饰器
    
    Args:
        default_per_page (int): 默认每页数量
        max_per_page (int): 最大每页数量
    
    Returns:
        function: 装饰器函数
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            try:
                page = int(request.args.get('page', 1))
                per_page = int(request.args.get('per_page', default_per_page))
                
                if page < 1:
                    return handle_api_error(400, "页码必须大于0")
                
                if per_page < 1 or per_page > max_per_page:
                    return handle_api_error(400, f"每页数量必须在1-{max_per_page}之间")
                
                # 将分页参数添加到请求上下文
                g.page = page
                g.per_page = per_page
                
            except ValueError:
                return handle_api_error(400, "分页参数必须是整数")
            
            return f(*args, **kwargs)
        return decorated_function
    return decorator