# API模块
from .v1 import register_blueprints as register_v1_blueprints

def register_all_apis(app):
    """注册所有API版本
    
    Args:
        app: Flask应用实例
    """
    # 注册v1 API
    register_v1_blueprints(app)

    # 注册水印工具 (独立模块)
    from .watermark import watermark_bp
    app.register_blueprint(watermark_bp)

    # 注册TTS V2模块
    from api.tts_v2 import tts_v2_bp
    app.register_blueprint(tts_v2_bp)

    # 注册豆包网页朗读捕获模块，与官方 WebSocket TTS 路线独立
    from api.doubao_browser_tts import doubao_browser_tts_bp
    app.register_blueprint(doubao_browser_tts_bp)

    # 注册研报 PDF Gem 解析模块，与官方 Gemini API 路线独立；保留旧路径兼容
    from api.gemini_browser import gemini_browser_bp, report_pdf_gemini_bp
    app.register_blueprint(report_pdf_gemini_bp)
    app.register_blueprint(gemini_browser_bp)

    # 注册研报 PDF -> 研报 PDF Gem -> 豆包浏览器朗读编排模块
    from api.report_audio_workflow import report_audio_workflow_bp
    app.register_blueprint(report_audio_workflow_bp)

    # 注册已同步 MP3 -> Gemini 图片 -> 横竖屏视频编排模块
    from api.mp3_video_workflow import mp3_video_workflow_bp
    app.register_blueprint(mp3_video_workflow_bp)
    
    # 创建API信息端点
    @app.route('/api', methods=['GET'])
    def api_info():
        """API信息"""
        endpoints = {
            'v1': {
                'base_url': '/api/v1',
                'auth': '/api/v1/auth',
                'channels': '/api/v1/channels',
                'rankings': '/api/v1/rankings',
                'data': '/api/v1/data',
                'videos': '/api/v1/videos',
                'documents': '/api/v1/documents'
            }
        }

        if app.config.get('ENABLE_TTS_API', False):
            endpoints['v1']['tts'] = '/api/v1/tts'
        endpoints['doubao_browser_tts'] = '/api/doubao-tts'
        endpoints['report_pdf_gemini'] = '/api/report-pdf-gemini'
        endpoints['gemini_browser_legacy'] = '/api/gemini-browser'
        endpoints['report_audio_workflows'] = '/api/report-audio-workflows'
        endpoints['mp3_video_workflows'] = '/api/mp3-video-workflows'
        if app.config.get('ENABLE_MHTML_API', False):
            endpoints['v1']['mhtml_pdf'] = '/api/v1/mhtml-pdf'

        return {
            'success': True,
            'data': {
                'name': 'YouTube Channel Tracker API',
                'version': '1.0.0',
                'versions': ['v1'],
                'endpoints': endpoints,
                'documentation': '/api/docs'  # TODO: 添加API文档
            }
        }

__all__ = [
    'register_all_apis'
]
