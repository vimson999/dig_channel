# 后端包初始化文件
"""
后端服务包

包含所有后端相关的代码：
- Flask应用配置和启动
- API路由和控制器
- 数据模型和数据库操作
- 业务逻辑服务
- 工具类和中间件
"""

from flask import Flask
from .config import Config

def create_app(config_class=Config):
    """Flask应用工厂函数
    
    Args:
        config_class: 配置类，默认为Config
        
    Returns:
        Flask: 配置好的Flask应用实例
    """
    app = Flask(__name__)
    app.config.from_object(config_class)
    
    # 这里将添加数据库、API等初始化代码
    
    return app