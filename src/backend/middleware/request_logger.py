"""
请求日志中间件
记录所有API请求/响应的详细信息，包括性能指标
"""
import time
import json
import uuid
from flask import request, g
from functools import wraps
from utils.colored_logger import setup_colored_logger, log_api_request

logger = setup_colored_logger(__name__)

class RequestLoggerMiddleware:
    def __init__(self, app=None):
        self.app = app
        if app is not None:
            self.init_app(app)
    
    def init_app(self, app):
        """初始化中间件"""
        app.before_request(self.before_request)
        app.after_request(self.after_request)
        app.teardown_appcontext(self.teardown_request)
    
    def before_request(self):
        """请求开始前记录"""
        # 生成请求ID
        g.request_id = str(uuid.uuid4())[:8]
        g.start_time = time.time()
        
        # 获取客户端信息
        client_ip = request.environ.get('HTTP_X_FORWARDED_FOR', request.remote_addr)
        user_agent = request.headers.get('User-Agent', 'Unknown')
        
        # 记录请求开始
        logger.info(f"🌐 [{g.request_id}] {request.method} {request.path} - IP: {client_ip}")
        
        # 记录请求参数（排除敏感信息）
        if request.args:
            safe_args = self._filter_sensitive_data(dict(request.args))
            logger.debug(f"📝 [{g.request_id}] Query params: {safe_args}")
        
        # 记录请求体（POST/PUT/PATCH）
        if request.method in ['POST', 'PUT', 'PATCH'] and request.is_json:
            try:
                safe_data = self._filter_sensitive_data(request.get_json())
                logger.debug(f"📄 [{g.request_id}] Request body: {json.dumps(safe_data, ensure_ascii=False)}")
            except Exception as e:
                logger.warning(f"⚠️ [{g.request_id}] 无法解析请求体: {e}")
    
    def after_request(self, response):
        """请求结束后记录"""
        if hasattr(g, 'request_id'):
            duration = round((time.time() - g.start_time) * 1000, 2)  # 毫秒
            
            # 根据状态码选择日志级别和颜色
            status_code = response.status_code
            if status_code < 400:
                level = 'info'
                status_icon = '✅'
            elif status_code < 500:
                level = 'warning'
                status_icon = '⚠️'
            else:
                level = 'error'
                status_icon = '❌'
            
            # 记录响应
            log_msg = f"{status_icon} [{g.request_id}] {status_code} - {duration}ms"
            getattr(logger, level)(log_msg)
            
            # 记录响应体（仅在调试模式下）
            if logger.isEnabledFor(10):  # DEBUG level
                try:
                    if response.is_json:
                        response_data = response.get_json()
                        safe_response = self._filter_sensitive_data(response_data)
                        logger.debug(f"📤 [{g.request_id}] Response: {json.dumps(safe_response, ensure_ascii=False)}")
                except Exception as e:
                    logger.debug(f"📤 [{g.request_id}] Response size: {len(response.data)} bytes")
            
            # 使用现有的API日志记录函数
            log_api_request(request.method, request.path, status_code, duration)
        
        return response
    
    def teardown_request(self, error):
        """请求结束时清理"""
        if error:
            request_id = getattr(g, 'request_id', 'unknown')
            logger.error(f"💥 [{request_id}] 请求异常: {error}")
    
    def _filter_sensitive_data(self, data):
        """过滤敏感数据"""
        if not isinstance(data, dict):
            return data
        
        sensitive_keys = {
            'password', 'passwd', 'pwd', 'secret', 'token', 'key', 'api_key',
            'access_token', 'refresh_token', 'auth_token', 'session_id'
        }
        
        filtered = {}
        for key, value in data.items():
            if key.lower() in sensitive_keys:
                filtered[key] = '*' * 8
            elif isinstance(value, dict):
                filtered[key] = self._filter_sensitive_data(value)
            else:
                filtered[key] = value
        
        return filtered

def log_request_performance(func):
    """装饰器：记录函数执行性能"""
    @wraps(func)
    def wrapper(*args, **kwargs):
        start_time = time.time()
        request_id = getattr(g, 'request_id', 'unknown')
        
        try:
            result = func(*args, **kwargs)
            duration = round((time.time() - start_time) * 1000, 2)
            logger.info(f"⚡ [{request_id}] {func.__name__} 执行耗时: {duration}ms")
            return result
        except Exception as e:
            duration = round((time.time() - start_time) * 1000, 2)
            logger.error(f"💥 [{request_id}] {func.__name__} 执行失败({duration}ms): {e}")
            raise
    
    return wrapper