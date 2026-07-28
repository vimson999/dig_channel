import logging
import os

from flask import Blueprint, jsonify, request, send_file
from sqlalchemy.orm import load_only

from models import db
from models.task import PublicationTask
from services.doubao_tts_ws import is_configured as is_doubao_tts_configured
from services.runtime_guard import apply_async_after_db_release
from tasks.tts import process_doubao_tts_task
from utils.auth_utils import require_login

logger = logging.getLogger("TTS_V2_API")

tts_v2_bp = Blueprint('tts_v2', __name__, url_prefix='/api/v2/tts')

TASK_PAYLOAD_COLUMNS = (
    PublicationTask.id,
    PublicationTask.celery_task_id,
    PublicationTask.status,
    PublicationTask.current_step,
    PublicationTask.progress,
    PublicationTask.params,
    PublicationTask.result,
    PublicationTask.error_message,
    PublicationTask.feature,
    PublicationTask.user_id,
    PublicationTask.task_title,
    PublicationTask.created_at,
    PublicationTask.updated_at,
    PublicationTask.completed_at,
)


def _is_tts_task(task):
    return bool((task.feature or (task.params or {}).get('feature')) == 'doubao_tts')


def _task_title(task):
    return (task.params or {}).get('tts', {}).get('title') or '豆包语音合成'


def _task_config(task):
    return (task.params or {}).get('tts', {}).get('config') or {}


def _task_segments(task):
    source_segments = (task.params or {}).get('tts', {}).get('segments') or []
    result_segments = (task.result or {}).get('segments') or []
    if result_segments:
        return result_segments
    return [
        {
            'sequence': index + 1,
            'text': text,
            'status': 'waiting',
            'audio_path': None,
            'subtitle_path': None,
            'error_log': None,
        }
        for index, text in enumerate(source_segments)
    ]


def _task_to_list_item(task):
    segments = _task_segments(task)
    successful_segments = [
        _segment_to_public(task.id, segment)
        for segment in segments
        if segment.get('status') == 'success' and segment.get('audio_path')
    ]
    return {
        'id': task.id,
        'title': _task_title(task),
        'status': task.status,
        'config': _task_config(task),
        'created_at': task.created_at.isoformat() if task.created_at else None,
        'updated_at': task.updated_at.isoformat() if task.updated_at else None,
        'segment_count': len((task.params or {}).get('tts', {}).get('segments') or []),
        'success_count': len(successful_segments),
        'first_audio_segment': successful_segments[0] if successful_segments else None,
    }


def _segment_to_public(task_id, segment):
    item = dict(segment)
    sequence = item.get('sequence')
    if item.get('audio_path'):
        item['audio_url'] = f'/api/v2/tts/task/{task_id}/segment/{sequence}/audio'
    if item.get('subtitle_path'):
        item['subtitle_url'] = f'/api/v2/tts/task/{task_id}/segment/{sequence}/subtitle'
    item.pop('audio_path', None)
    item.pop('subtitle_path', None)
    return item


@tts_v2_bp.route('/status', methods=['GET'])
@require_login
def get_tts_status(current_user):
    """获取TTS服务配置状态"""
    return jsonify({
        'success': True,
        'data': {
            'providers': [
                {
                    'id': 'doubao_ws_v3',
                    'name': '豆包语音 WebSocket V3',
                    'configured': is_doubao_tts_configured(),
                    'resource_id': 'seed-tts-2.0',
                    'formats': ['mp3', 'ogg_opus', 'pcm']
                }
            ]
        }
    })


@tts_v2_bp.route('/submit', methods=['POST'])
@require_login
def submit_tts_task(current_user):
    """提交豆包TTS合成任务，复用 publication_tasks。"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': '请求数据缺失'}), 400

        title = (data.get('title') or '').strip()
        segments_text = [
            str(item).strip()
            for item in data.get('segments', [])
            if str(item).strip()
        ]
        config = data.get('config', {})
        if not isinstance(config, dict):
            config = {}
        config['provider'] = 'doubao_ws_v3'

        if not title:
            return jsonify({'success': False, 'message': '标题不能为空'}), 400
        if not segments_text:
            return jsonify({'success': False, 'message': '段落内容不能为空'}), 400
        if not is_doubao_tts_configured() and not config.get('api_key'):
            return jsonify({'success': False, 'message': '豆包 TTS API Key 未配置'}), 400

        task = PublicationTask(
            status='pending',
            current_step='init',
            progress=0,
            params={
                'feature': 'doubao_tts',
                'user_id': current_user.id,
                'tts': {
                    'title': title,
                    'segments': segments_text,
                    'config': config,
                },
            },
            result={
                'segments': [
                    {
                        'sequence': index + 1,
                        'text': text,
                        'status': 'waiting',
                        'audio_path': None,
                        'subtitle_path': None,
                        'error_log': None,
                    }
                    for index, text in enumerate(segments_text)
                ]
            },
        )
        db.session.add(task)
        db.session.commit()

        try:
            celery_task = apply_async_after_db_release(
                process_doubao_tts_task,
                args=[task.id],
                queue='cloud_tts',
                retry=False,
            )
        except Exception as exc:
            logger.exception("提交TTS任务入队失败: %s", task.id)
            task.status = 'failed'
            task.current_step = 'failed'
            task.error_message = f'TTS任务入队失败: {exc}'
            db.session.commit()
            return jsonify({'success': False, 'message': task.error_message}), 502

        task.celery_task_id = celery_task.id
        db.session.commit()

        return jsonify({
            'success': True,
            'message': '任务已提交，正在后台排队处理',
            'data': {'article_id': task.id, 'task_id': task.id, 'celery_id': celery_task.id}
        })

    except Exception as exc:
        db.session.rollback()
        logger.error("提交TTS任务失败: %s", exc, exc_info=True)
        return jsonify({'success': False, 'message': f'系统错误: {exc}'}), 500


@tts_v2_bp.route('/history', methods=['GET'])
@require_login
def get_history(current_user):
    """获取历史任务列表"""
    try:
        page = max(1, request.args.get('page', 1, type=int))
        per_page = min(50, max(1, request.args.get('per_page', 10, type=int)))

        query = PublicationTask.query.filter(
            PublicationTask.feature == 'doubao_tts',
            PublicationTask.user_id == str(current_user.id),
        ).order_by(PublicationTask.created_at.desc())
        pagination = query.options(load_only(*TASK_PAYLOAD_COLUMNS)).paginate(page=page, per_page=per_page, error_out=False)

        return jsonify({
            'success': True,
            'data': {
                'items': [_task_to_list_item(task) for task in pagination.items],
                'total': pagination.total,
                'pages': pagination.pages,
                'current_page': pagination.page
            }
        })
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 500


@tts_v2_bp.route('/article/<task_id>', methods=['GET'])
@require_login
def get_article_detail(current_user, task_id):
    """获取任务详情及所有段落状态"""
    try:
        task = _get_tts_task(task_id)
        if not task or str(task.user_id or (task.params or {}).get('user_id')) != str(current_user.id):
            return jsonify({'success': False, 'message': '未找到该任务'}), 404

        data = _task_to_list_item(task)
        data['segments'] = [_segment_to_public(task.id, segment) for segment in _task_segments(task)]
        return jsonify({'success': True, 'data': data})
    except Exception as exc:
        return jsonify({'success': False, 'message': str(exc)}), 500


def _find_segment(task_id, sequence):
    task = _get_tts_task(task_id)
    if not task or not _is_tts_task(task):
        return None, None
    for segment in _task_segments(task):
        if int(segment.get('sequence') or 0) == int(sequence):
            return task, segment
    return task, None


def _get_tts_task(task_id):
    return PublicationTask.query.options(load_only(*TASK_PAYLOAD_COLUMNS)).filter(PublicationTask.id == str(task_id)).one_or_none()


@tts_v2_bp.route('/task/<task_id>/segment/<int:sequence>/audio', methods=['GET'])
@require_login
def download_segment_audio(current_user, task_id, sequence):
    task, segment = _find_segment(task_id, sequence)
    if not task or not segment or str((task.params or {}).get('user_id')) != str(current_user.id):
        return jsonify({'success': False, 'message': '未找到音频'}), 404
    audio_path = segment.get('audio_path')
    if not audio_path or not os.path.exists(audio_path):
        return jsonify({'success': False, 'message': '音频文件不存在'}), 404
    return send_file(audio_path, as_attachment=True)


@tts_v2_bp.route('/task/<task_id>/segment/<int:sequence>/subtitle', methods=['GET'])
@require_login
def download_segment_subtitle(current_user, task_id, sequence):
    task, segment = _find_segment(task_id, sequence)
    if not task or not segment or str((task.params or {}).get('user_id')) != str(current_user.id):
        return jsonify({'success': False, 'message': '未找到字幕'}), 404
    subtitle_path = segment.get('subtitle_path')
    if not subtitle_path or not os.path.exists(subtitle_path):
        return jsonify({'success': False, 'message': '字幕文件不存在'}), 404
    return send_file(subtitle_path, as_attachment=True, mimetype='text/plain')
