# 带颜色的控制台日志工具
"""
带颜色的控制台日志输出工具

提供不同级别的彩色日志输出功能
"""

import logging
import sys
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
    
    # 避免重复添加handler
    if logger.handlers:
        return logger
    
    # 创建控制台handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    
    # 设置彩色格式化器
    formatter = ColoredFormatter()
    console_handler.setFormatter(formatter)
    
    # 添加handler到logger
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

# 预配置的日志器
app_logger = setup_colored_logger("APP")
api_logger = setup_colored_logger("API")
db_logger = setup_colored_logger("DATABASE")
youtube_logger = setup_colored_logger("YOUTUBE")