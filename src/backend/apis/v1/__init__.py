# API v1 模块
from .auth import bp as auth_bp
from .channels import bp as channels_bp
from .rankings import bp as rankings_bp  
from .data import bp as data_bp
from .scheduler import bp as scheduler_bp
from .trending import bp as trending_bp
from .favorites import bp as favorites_bp
from .documents import bp as documents_bp

def register_blueprints(app):
    """注册所有API蓝图
    
    Args:
        app: Flask应用实例
    """
    app.register_blueprint(auth_bp)
    app.register_blueprint(channels_bp)
    app.register_blueprint(rankings_bp)
    app.register_blueprint(data_bp)
    app.register_blueprint(scheduler_bp)
    app.register_blueprint(trending_bp)
    app.register_blueprint(favorites_bp)
    app.register_blueprint(documents_bp)
    
    # 注册视频API
    from api.videos import videos_bp
    app.register_blueprint(videos_bp)

__all__ = [
    'auth_bp',
    'channels_bp',
    'rankings_bp',
    'data_bp',
    'scheduler_bp',
    'trending_bp',
    'favorites_bp',
    'documents_bp',
    'register_blueprints'
]