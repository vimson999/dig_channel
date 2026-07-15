# API v1 模块
from .auth import bp as auth_bp
from .channels import bp as channels_bp
from .rankings import bp as rankings_bp  
from .data import bp as data_bp
from .scheduler import bp as scheduler_bp
from .trending import bp as trending_bp
from .favorites import bp as favorites_bp
from .documents import bp as documents_bp
from .gemini import bp as gemini_bp
from .batch_transcription import bp as batch_transcription_bp

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
    app.register_blueprint(gemini_bp)
    app.register_blueprint(batch_transcription_bp)
    
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

    # 注册Bilibili API
    from .bilibili import bp as bilibili_bp
    app.register_blueprint(bilibili_bp)

    # 注册视频剪辑 API (新增)
    from .video_editor import bp as video_editor_bp
    app.register_blueprint(video_editor_bp)

    # 注册研报封面生成 API (新增)
    from .thumbnail import bp as thumbnail_bp
    app.register_blueprint(thumbnail_bp)

    # 注册一键发布 API (新增)
    from .publisher import bp as publisher_bp
    app.register_blueprint(publisher_bp)

    # 注册资源管理 API (新增)
    from .resources import bp as resources_bp
    app.register_blueprint(resources_bp)

    # 注册视频下载 API (新增)
    from .downloader import bp as downloader_bp
    app.register_blueprint(downloader_bp)

    # 注册知识星球试验 API
    from .zsxq import bp as zsxq_bp
    app.register_blueprint(zsxq_bp)

    # 注册知识星球情报分析 API
    from .zsxq_insights import bp as zsxq_insights_bp
    app.register_blueprint(zsxq_insights_bp)

    # 注册外链文档富化 API
    from .external_documents import bp as external_documents_bp
    app.register_blueprint(external_documents_bp)

    # 注册 Celery Worker 控制台 API
    from .celery_monitor import bp as celery_monitor_bp
    app.register_blueprint(celery_monitor_bp)

    # 注册研报口播/导图视频工厂 API
    from .report_video import bp as report_video_bp
    app.register_blueprint(report_video_bp)

    # 注册内容流水线 API
    from .content_pipeline import bp as content_pipeline_bp
    app.register_blueprint(content_pipeline_bp)

    # 注册平台凭据管理 API
    from .platform_credentials import bp as platform_credentials_bp
    app.register_blueprint(platform_credentials_bp)

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
