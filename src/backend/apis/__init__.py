# API模块
from .v1 import register_blueprints as register_v1_blueprints

def register_all_apis(app):
    """注册所有API版本
    
    Args:
        app: Flask应用实例
    """
    # 注册v1 API
    register_v1_blueprints(app)
    from api.audio_transcription import bp as audio_transcription_bp
    app.register_blueprint(audio_transcription_bp)

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

    # 注册多 PDF 合并为可复用系统资产的独立工作流
    from api.pdf_merge_workflow import pdf_merge_workflow_bp
    app.register_blueprint(pdf_merge_workflow_bp)

    # 注册今日研报 Top10 总编辑编排模块
    from api.report_top10_workflow import report_top10_workflow_bp
    app.register_blueprint(report_top10_workflow_bp)

    # 注册研报基础文案物料独立编排模块
    from api.report_script_material_workflow import report_script_material_workflow_bp
    app.register_blueprint(report_script_material_workflow_bp)

    # PDF 证据映射：独立于 Gemini 传输和 TTS 文本，供所有视觉方案复用
    from api.report_pdf_evidence import report_pdf_evidence_bp
    app.register_blueprint(report_pdf_evidence_bp)

    # 注册研报视觉编排预览模块
    from api.report_visual_plan import report_visual_plan_bp
    app.register_blueprint(report_visual_plan_bp)

    # 注册研报混合媒介导演总控；生产完整渲染保持人工放行和失败关闭
    from api.report_video_director_workflow import report_video_director_workflow_bp
    app.register_blueprint(report_video_director_workflow_bp)

    # 注册研报 PDF -> 火柴人视频父编排；基础文案和分镜是它的子阶段
    from api.report_stick_figure_video_workflow import report_stick_figure_video_workflow_bp
    app.register_blueprint(report_stick_figure_video_workflow_bp)

    # 用户文案 -> 豆包语音 -> Gemini 图片 -> MP4 的通用分步工作流
    from api.script_video_workflow import script_video_workflow_bp
    app.register_blueprint(script_video_workflow_bp)

    # 注册已同步 MP3 -> Gemini 图片 -> 横竖屏视频编排模块
    from api.mp3_video_workflow import mp3_video_workflow_bp
    app.register_blueprint(mp3_video_workflow_bp)

    # 注册通用 B站发布辅助接口，供多个详情页复用
    from api.bilibili_publish import bilibili_publish_bp
    app.register_blueprint(bilibili_publish_bp)
    
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
        endpoints['pdf_merge_workflows'] = '/api/pdf-merge-workflows'
        endpoints['report_top10_workflows'] = '/api/report-top10-workflows'
        endpoints['report_script_material_workflows'] = '/api/report-script-material-workflows'
        endpoints['report_pdf_evidence'] = '/api/report-pdf-evidence'
        endpoints['report_visual_plans'] = '/api/report-visual-plans'
        endpoints['report_video_director_workflows'] = '/api/report-video-director-workflows'
        endpoints['report_stick_figure_video_workflows'] = '/api/report-stick-figure-video-workflows'
        endpoints['script_video_workflows'] = '/api/script-video-workflows'
        endpoints['mp3_video_workflows'] = '/api/mp3-video-workflows'
        endpoints['bilibili_publish'] = '/api/bilibili-publish'
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
