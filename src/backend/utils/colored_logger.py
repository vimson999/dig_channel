# 带颜色的控制台日志工具
"""
带颜色的控制台日志输出工具

提供不同级别的彩色日志输出功能
"""

import logging
import sys
import os
from datetime import datetime

class ColoredFormatter(logging.Formatter):
    """带颜色的日志格式化器"""
    
    # ANSI颜色代码
    COLORS = {
        'DEBUG': '\033[36m',      # 青色
        'INFO': '\033[32m',       # 绿色
        'WARNING': '\033[33m',    # 黄色
        'ERROR': '\033[31m',      # 红色
        'CRITICAL': '\033[35m',   # 紫色
        'RESET': '\033[0m'        # 重置
    }
    
    # 日志级别对应的emoji
    EMOJIS = {
        'DEBUG': '🔍',
        'INFO': '✅',
        'WARNING': '⚠️',
        'ERROR': '❌',
        'CRITICAL': '🚨'
    }
    
    def format(self, record):
        # 获取颜色和emoji
        color = self.COLORS.get(record.levelname, self.COLORS['RESET'])
        emoji = self.EMOJIS.get(record.levelname, '📝')
        reset = self.COLORS['RESET']
        
        # 格式化时间
        timestamp = datetime.fromtimestamp(record.created).strftime('%Y-%m-%d %H:%M:%S')
        
        # 格式化消息
        message = super().format(record)
        
        # 组合最终输出
        return f"{color}{emoji} [{timestamp}] {record.levelname:8} - {record.name:20} - {message}{reset}"

def setup_colored_logger(name: str = None, level: int = logging.INFO) -> logging.Logger:
    """设置带颜色的日志器
    
    Args:
        name: 日志器名称
        level: 日志级别
    
    Returns:
        配置好的日志器
    """
    logger = logging.getLogger(name or __name__)
    logger.setLevel(level)

    # 检查是否已存在控制台handler
    has_console_handler = False
    for handler in logger.handlers:
        if isinstance(handler, logging.StreamHandler) and getattr(handler, 'stream', None) is sys.stdout:
            has_console_handler = True
            handler.setLevel(level)
            handler.setFormatter(ColoredFormatter())
            break

    if not has_console_handler:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_handler.setFormatter(ColoredFormatter())
        logger.addHandler(console_handler)

    return logger

def log_startup_banner(app_name: str = "YouTube频道数据追踪工具", version: str = "1.0.0"):
    """显示启动横幅"""
    logger = setup_colored_logger("STARTUP")
    
    banner = f"""
    \033[36m
    ╔═══════════════════════════════════════════════════════════════╗
    ║                                                               ║
    ║  🚀 {app_name:<50} ║
    ║  📊 版本: {version:<50} ║
    ║  🌐 后端服务正在启动...                                       ║
    ║                                                               ║
    ╚═══════════════════════════════════════════════════════════════╝
    \033[0m
    """
    print(banner)
    logger.info("应用启动中...")

def log_api_request(method: str, path: str, status_code: int, duration: float = None):
    """记录API请求"""
    logger = setup_colored_logger("API")
    
    # 根据状态码选择日志级别和颜色
    if status_code < 300:
        emoji = "🟢"
        level = "info"
    elif status_code < 400:
        emoji = "🟡"
        level = "warning"
    else:
        emoji = "🔴"
        level = "error"
    
    duration_str = f" ({duration:.2f}ms)" if duration else ""
    message = f"{emoji} {method:6} {path:30} -> {status_code}{duration_str}"
    
    getattr(logger, level)(message)

def log_database_operation(operation: str, table: str, success: bool = True, details: str = None):
    """记录数据库操作"""
    logger = setup_colored_logger("DATABASE")
    
    emoji = "💾" if success else "💥"
    level = "info" if success else "error"
    status = "成功" if success else "失败"
    
    message = f"{emoji} {operation} {table} - {status}"
    if details:
        message += f" ({details})"
    
    getattr(logger, level)(message)

def log_youtube_sync(channel_name: str, success: bool = True, details: str = None):
    """记录YouTube同步操作"""
    logger = setup_colored_logger("YOUTUBE")
    
    emoji = "📺" if success else "🚫"
    level = "info" if success else "error"
    status = "同步成功" if success else "同步失败"
    
    message = f"{emoji} {channel_name} - {status}"
    if details:
        message += f" ({details})"
    
    getattr(logger, level)(message)

def log_detailed_error(logger_name: str, context: str, error: Exception, **kwargs):
    """记录详细错误信息到文件和控制台
    
    Args:
        logger_name: 日志器名称
        context: 错误上下文描述
        error: 异常对象
        **kwargs: 额外的上下文信息
    """
    import traceback
    
    logger = setup_colored_logger(logger_name)
    
    # 创建logs目录
    logs_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'logs')
    os.makedirs(logs_dir, exist_ok=True)
    
    # 生成错误日志文件名
    timestamp = datetime.now().strftime("%Y%m%d")
    error_log_file = os.path.join(logs_dir, f"errors_{timestamp}.log")
    
    # 错误详情
    error_details = {
        'timestamp': datetime.now().isoformat(),
        'context': context,
        'error_type': type(error).__name__,
        'error_message': str(error),
        'traceback': traceback.format_exc(),
        'additional_info': kwargs
    }
    
    # 格式化错误消息
    error_msg = f"""
{'='*80}
时间: {error_details['timestamp']}
上下文: {error_details['context']}
错误类型: {error_details['error_type']}
错误信息: {error_details['error_message']}
额外信息: {error_details['additional_info']}
堆栈跟踪:
{error_details['traceback']}
{'='*80}
"""
    
    # 写入文件
    try:
        with open(error_log_file, 'a', encoding='utf-8') as f:
            f.write(error_msg)
    except Exception as e:
        print(f"无法写入错误日志文件: {e}")
    
    # 控制台输出
    logger.error(f"❌ {context}")
    logger.error(f"   错误类型: {type(error).__name__}")
    logger.error(f"   错误信息: {str(error)}")
    for key, value in kwargs.items():
        logger.error(f"   {key}: {value}")
    logger.error(f"   详细日志已写入: {error_log_file}")
    
    return error_details

# 预配置的日志器
app_logger = setup_colored_logger("APP")
api_logger = setup_colored_logger("API")
db_logger = setup_colored_logger("DATABASE")
youtube_logger = setup_colored_logger("YOUTUBE")
