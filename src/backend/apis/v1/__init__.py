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
    
    # 注册TTS API（可配置）
    if app.config.get('ENABLE_TTS_API', False):
        from api.tts import tts_bp
        app.register_blueprint(tts_bp)
        app.logger.info('TTS API 已启用')
    else:
        app.logger.info('TTS API 已禁用，跳过注册')

    # 注册MHTML转PDF API（可配置）
    if app.config.get('ENABLE_MHTML_API', False):
        from api.mhtml_pdf import mhtml_pdf_bp
        app.register_blueprint(mhtml_pdf_bp)
        app.logger.info('MHTML 转 PDF API 已启用')
    else:
        app.logger.info('MHTML 转 PDF API 已禁用，跳过注册')
    
    # 注册学生证生成器API
    from api.student_id_generator import student_id_bp
    app.register_blueprint(student_id_bp)
    
    # 注册独立开发者领航员API
    from api.indie_navigator import indie_navigator_bp
    app.register_blueprint(indie_navigator_bp)
    
    # 注册数据采集API
    from api.data_collection import data_collection_bp
    app.register_blueprint(data_collection_bp)

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
