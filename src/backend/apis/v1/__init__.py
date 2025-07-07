# API v1 模块
from .channels import bp as channels_bp
from .rankings import bp as rankings_bp  
from .data import bp as data_bp
from .scheduler import bp as scheduler_bp

def register_blueprints(app):
    """注册所有API蓝图
    
    Args:
        app: Flask应用实例
    """
    app.register_blueprint(channels_bp)
    app.register_blueprint(rankings_bp)
    app.register_blueprint(data_bp)
    app.register_blueprint(scheduler_bp)

__all__ = [
    'channels_bp',
    'rankings_bp',
    'data_bp',
    'scheduler_bp',
    'register_blueprints'
]