# 错误处理中间件
"""
全局错误处理和用户友好的错误响应

提供统一的错误处理、日志记录和用户友好的错误消息
"""

import logging
from typing import Dict, Any, Optional
from datetime import datetime
from flask import request, jsonify
from werkzeug.exceptions import HTTPException
from sqlalchemy.exc import SQLAlchemyError, IntegrityError
from utils.api_utils import handle_api_error
from utils.security_logging import redacted_request_path

# 配置日志
logger = logging.getLogger(__name__)

class ErrorHandler:
    """错误处理器类"""
    
    def __init__(self, app=None):
        self.app = app
        if app is not None:
            self.init_app(app)
    
    def init_app(self, app):
        """初始化错误处理器"""
        app.register_error_handler(Exception, self.handle_exception)
        app.register_error_handler(HTTPException, self.handle_http_exception)
        app.register_error_handler(SQLAlchemyError, self.handle_database_error)
        app.register_error_handler(404, self.handle_not_found)
        app.register_error_handler(405, self.handle_method_not_allowed)
        app.register_error_handler(500, self.handle_internal_server_error)
    
    def handle_exception(self, error):
        """处理通用异常"""
        error_id = self._generate_error_id()
        
        # 记录详细错误信息
        logger.error(
            "[%s] 未处理异常 type=%s method=%s path=%s",
            error_id,
            type(error).__name__,
            request.method,
            redacted_request_path(),
        )
        
        # 根据错误类型返回不同消息
        user_message = self._get_user_friendly_message(error)
        
        return handle_api_error(
            500,
            user_message,
            {
                'error_id': error_id,
                'error_type': type(error).__name__,
                'timestamp': datetime.now().isoformat()
            }
        )
    
    def handle_http_exception(self, error):
        """处理HTTP异常"""
        error_id = self._generate_error_id()
        
        logger.warning(f"[{error_id}] HTTP异常: {error.code} - {error.description}")
        
        # HTTP状态码对应的用户友好消息
        status_messages = {
            400: "请求参数错误，请检查输入数据",
            401: "未授权访问，请先登录",
            403: "访问被拒绝，权限不足",
            404: "请求的资源不存在",
            405: "请求方法不被允许",
            409: "资源冲突，请刷新后重试",
            422: "数据验证失败，请检查输入格式",
            429: "请求过于频繁，请稍后重试"
        }
        
        user_message = status_messages.get(error.code, error.description)
        
        return handle_api_error(
            error.code,
            user_message,
            {
                'error_id': error_id,
                'timestamp': datetime.now().isoformat()
            }
        )
    
    def handle_database_error(self, error):
        """处理数据库异常"""
        error_id = self._generate_error_id()
        
        logger.error("[%s] 数据库错误 type=%s", error_id, type(error).__name__)
        
        # 根据数据库错误类型返回不同消息
        if isinstance(error, IntegrityError):
            if "UNIQUE constraint failed" in str(error):
                user_message = "数据已存在，不能重复添加"
            elif "NOT NULL constraint failed" in str(error):
                user_message = "必填字段不能为空"
            elif "FOREIGN KEY constraint failed" in str(error):
                user_message = "关联数据不存在，请检查相关记录"
            else:
                user_message = "数据完整性错误，请检查输入数据"
        else:
            user_message = "数据库操作失败，请稍后重试"
        
        return handle_api_error(
            500,
            user_message,
            {
                'error_id': error_id,
                'error_type': 'DatabaseError',
                'timestamp': datetime.now().isoformat()
            }
        )
    
    def handle_not_found(self, error):
        """处理404错误"""
        error_id = self._generate_error_id()
        
        logger.warning(f"[{error_id}] 404错误: {redacted_request_path()}")
        
        # 根据路径提供更具体的消息
        if request.path.startswith('/api/'):
            user_message = "API接口不存在，请检查请求路径"
        else:
            user_message = "请求的页面或资源不存在"
        
        return handle_api_error(
            404,
            user_message,
            {
                'error_id': error_id,
                'requested_path': redacted_request_path(),
                'timestamp': datetime.now().isoformat()
            }
        )
    
    def handle_method_not_allowed(self, error):
        """处理405错误"""
        error_id = self._generate_error_id()
        
        logger.warning(f"[{error_id}] 405错误: {request.method} {redacted_request_path()}")
        
        return handle_api_error(
            405,
            f"请求方法 {request.method} 不被允许",
            {
                'error_id': error_id,
                'allowed_methods': list(error.valid_methods) if hasattr(error, 'valid_methods') else [],
                'timestamp': datetime.now().isoformat()
            }
        )
    
    def handle_internal_server_error(self, error):
        """处理500错误"""
        error_id = self._generate_error_id()
        
        logger.error("[%s] 500错误 type=%s", error_id, type(error).__name__)
        
        return handle_api_error(
            500,
            "服务器内部错误，我们已记录此问题，请稍后重试",
            {
                'error_id': error_id,
                'timestamp': datetime.now().isoformat()
            }
        )
    
    def _generate_error_id(self) -> str:
        """生成错误ID用于追踪"""
        import uuid
        return str(uuid.uuid4())[:8]
    
    def _get_user_friendly_message(self, error) -> str:
        """根据异常类型返回用户友好的错误消息"""
        error_type = type(error).__name__
        
        # 常见异常的用户友好消息
        friendly_messages = {
            'ConnectionError': "网络连接失败，请检查网络设置",
            'TimeoutError': "操作超时，请稍后重试",
            'ValueError': "数据格式错误，请检查输入",
            'KeyError': "缺少必要的数据字段",
            'TypeError': "数据类型错误",
            'FileNotFoundError': "文件不存在",
            'PermissionError': "权限不足，无法执行操作",
            'JSONDecodeError': "JSON数据格式错误",
            'ValidationError': "数据验证失败，请检查输入格式"
        }
        
        # 检查错误消息中的关键词
        error_message = str(error).lower()
        if 'youtube' in error_message and 'api' in error_message:
            return "YouTube API调用失败，请检查API密钥配置"
        elif 'database' in error_message or 'sql' in error_message:
            return "数据库操作失败，请稍后重试"
        elif 'network' in error_message or 'connection' in error_message:
            return "网络连接问题，请检查网络设置"
        elif 'permission' in error_message or 'access' in error_message:
            return "权限不足，请联系管理员"
        
        return friendly_messages.get(error_type, "系统发生错误，请稍后重试")

# 全局错误处理实例
error_handler = ErrorHandler()

# 业务异常基类
class BusinessException(Exception):
    """业务异常基类"""
    
    def __init__(self, message: str, code: int = 400, details: Optional[Dict[str, Any]] = None):
        self.message = message
        self.code = code
        self.details = details or {}
        super().__init__(self.message)

class ValidationException(BusinessException):
    """数据验证异常"""
    pass

class ResourceNotFoundException(BusinessException):
    """资源未找到异常"""
    
    def __init__(self, resource_type: str, resource_id: Any = None):
        if resource_id:
            message = f"{resource_type} (ID: {resource_id}) 不存在"
        else:
            message = f"{resource_type} 不存在"
        super().__init__(message, 404)

class DuplicateResourceException(BusinessException):
    """资源重复异常"""
    
    def __init__(self, resource_type: str, field: str = None):
        if field:
            message = f"{resource_type} 已存在 ({field} 重复)"
        else:
            message = f"{resource_type} 已存在"
        super().__init__(message, 409)

class ExternalServiceException(BusinessException):
    """外部服务异常"""
    
    def __init__(self, service_name: str, message: str = None):
        if message:
            full_message = f"{service_name} 服务异常: {message}"
        else:
            full_message = f"{service_name} 服务暂时不可用"
        super().__init__(full_message, 503)

# 便捷函数
def raise_validation_error(message: str, details: Dict[str, Any] = None):
    """抛出验证错误"""
    raise ValidationException(message, details=details)

def raise_not_found_error(resource_type: str, resource_id: Any = None):
    """抛出资源未找到错误"""
    raise ResourceNotFoundException(resource_type, resource_id)

def raise_duplicate_error(resource_type: str, field: str = None):
    """抛出资源重复错误"""
    raise DuplicateResourceException(resource_type, field)

def raise_service_error(service_name: str, message: str = None):
    """抛出外部服务错误"""
    raise ExternalServiceException(service_name, message)
