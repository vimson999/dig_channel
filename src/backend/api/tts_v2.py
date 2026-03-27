from flask import Blueprint, request, jsonify, current_app
from datetime import datetime
import logging

from models import db
from models.tts import TTSArticle, TTSSegment
from tasks.tts import process_tts_article
from utils.auth_utils import require_login

logger = logging.getLogger("TTS_V2_API")

tts_v2_bp = Blueprint('tts_v2', __name__, url_prefix='/api/v2/tts')

@tts_v2_bp.route('/submit', methods=['POST'])
@require_login
def submit_tts_task(current_user):
    """提交TTS合成任务"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': '请求数据缺失'}), 400
        
        title = data.get('title', '').strip()
        segments_text = data.get('segments', []) # 文本数组
        config = data.get('config', {}) # 包含 voice_type, speed 等
        
        if not title:
            return jsonify({'success': False, 'message': '标题不能为空'}), 400
        if not segments_text:
            return jsonify({'success': False, 'message': '段落内容不能为空'}), 400

        # 1. 创建文章记录
        article = TTSArticle(
            title=title,
            user_id=current_user.id,
            status='pending',
            config=config
        )
        db.session.add(article)
        db.session.flush() # 获取 article.id

        # 2. 创建段落记录
        for i, text in enumerate(segments_text):
            segment = TTSSegment(
                article_id=article.id,
                sequence=i + 1,
                text=text.strip(),
                status='waiting'
            )
            db.session.add(segment)
        
        db.session.commit()

        # 3. 异步触发Celery任务
        process_tts_article.delay(article.id)

        return jsonify({
            'success': True,
            'message': '任务已提交，正在后台排队处理',
            'data': {'article_id': article.id}
        })

    except Exception as e:
        db.session.rollback()
        logger.error(f"提交TTS任务失败: {str(e)}")
        return jsonify({'success': False, 'message': f'系统错误: {str(e)}'}), 500

@tts_v2_bp.route('/history', methods=['GET'])
@require_login
def get_history(current_user):
    """获取历史任务列表"""
    try:
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 10, type=int)
        
        articles = TTSArticle.query.filter_by(user_id=current_user.id)\
            .order_by(TTSArticle.created_at.desc())\
            .paginate(page=page, per_page=per_page)
        
        return jsonify({
            'success': True,
            'data': {
                'items': [a.to_dict() for a in articles.items],
                'total': articles.total,
                'pages': articles.pages,
                'current_page': articles.page
            }
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500

@tts_v2_bp.route('/article/<article_id>', methods=['GET'])
@require_login
def get_article_detail(current_user, article_id):
    """获取文章详情及所有段落状态"""
    try:
        article = TTSArticle.query.filter_by(id=article_id, user_id=current_user.id).first()
        if not article:
            return jsonify({'success': False, 'message': '未找到该任务'}), 404
        
        segments = TTSSegment.query.filter_by(article_id=article_id).order_by(TTSSegment.sequence).all()
        
        data = article.to_dict()
        data['segments'] = [s.to_dict() for s in segments]
        
        return jsonify({
            'success': True,
            'data': data
        })
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500
