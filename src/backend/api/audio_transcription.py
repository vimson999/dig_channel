"""单文件音频转写接口；提供所有权隔离、窄状态查询与受控结果下载。"""
import logging
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from flask import Blueprint, abort, jsonify, request, send_file
from models import db
from models.task import PublicationTask
from services import audio_transcription as service
from services.runtime_guard import end_db_transaction_before_external_io, maybe_guarded_apply_async, release_db_session_quietly
from tasks.audio_transcription import process_job
from utils.auth_utils import require_login

bp = Blueprint('audio_transcription', __name__, url_prefix='/api/audio-transcriptions')
logger = logging.getLogger(__name__)


def owned(user, task_id=None):
    query = db.session.query(PublicationTask).filter(
        PublicationTask.feature == service.FEATURE_NAME,
        PublicationTask.user_id == str(user.id))
    return query.filter(PublicationTask.id == task_id) if task_id else query


def summary(row):
    return {key: (value.isoformat() + 'Z' if isinstance(value, datetime) else value)
            for key, value in row._mapping.items()}


def summaries(user, task_id=None):
    return owned(user, task_id).with_entities(
        PublicationTask.id, PublicationTask.task_title,
        PublicationTask.status, PublicationTask.current_step,
        PublicationTask.progress, PublicationTask.error_message,
        PublicationTask.created_at, PublicationTask.updated_at)


def enqueue(task_id, delivery_id):
    try:
        maybe_guarded_apply_async(process_job, args=[task_id], queue=service.QUEUE_NAME,
            feature=service.FEATURE_NAME, task_id=task_id, broker_task_id=delivery_id, retry=False)
    except Exception:
        logger.exception('音频转写投递失败 task=%s', task_id)
        release_db_session_quietly()
        PublicationTask.query.filter_by(id=task_id, celery_task_id=delivery_id, status='pending').update(
            {'status': 'failed', 'current_step': 'enqueue_failed',
             'error_message': '任务入队失败，请稍后重试。'}, synchronize_session=False)
        db.session.commit()
        return False
    finally:
        release_db_session_quietly()
    return True


@bp.get('/status')
@require_login
def status(user):
    return jsonify(success=True, data={'max_upload_bytes': service.max_upload_bytes()})


@bp.post('/jobs')
@require_login
def create(user):
    owner = str(user.id)
    end_db_transaction_before_external_io('audio transcription upload', remove=True)
    task_id, delivery_id = str(uuid.uuid4()), str(uuid.uuid4())
    path = None
    try:
        if request.content_length and request.content_length > service.max_upload_bytes() + 1024 * 1024:
            raise service.UploadTooLarge('音频超过上传大小限制')
        files = [value for _, value in request.files.items(multi=True)]
        if len(files) != 1:
            raise ValueError('请上传一个音频文件')
        upload = files[0]
        filename = service.normalize_filename(upload.filename)
        prompt = request.form.get('prompt', '').strip()
        service.recognition_prompt(prompt)
        path = service.task_root(task_id) / ('source' + Path(filename).suffix.lower())
        metadata = service.save_upload(upload.stream, path)
        duration = service.probe_audio(path)
        params = {'feature': service.FEATURE_NAME, 'user_id': owner, service.FEATURE_NAME: {
            'source_path': str(path), 'source_bytes': metadata['bytes'],
            'source_sha256': metadata['sha256'], 'original_filename': filename,
            'initial_prompt': prompt, 'duration_seconds': duration}}
        task = PublicationTask(id=task_id, celery_task_id=delivery_id,
            params=params, result={}, status='pending', current_step='queued', progress=0)
        task.task_title = filename
        db.session.add(task)
        db.session.commit()
    except Exception as exc:
        release_db_session_quietly()
        if path is not None:
            path.unlink(missing_ok=True)
        if isinstance(exc, ValueError):
            return jsonify(success=False, message=str(exc)), 413 if isinstance(exc, service.UploadTooLarge) else 400
        logger.exception('音频上传失败')
        return jsonify(success=False, message='音频保存失败，请稍后重试'), 500
    release_db_session_quietly()
    queued = enqueue(task_id, delivery_id)
    return jsonify(success=queued, data={'id': task_id},
                   message='已提交转写' if queued else '任务入队失败，可从历史记录重试'), 202 if queued else 502


@bp.get('/jobs')
@require_login
def history(user):
    try:
        page = max(1, min(100000, int(request.args.get('page', 1))))
    except ValueError:
        page = 1
    rows = summaries(user).order_by(PublicationTask.created_at.desc(), PublicationTask.id.desc()).offset((page-1)*20).limit(21).all()
    payload = {'items': [summary(row) for row in rows[:20]], 'has_more': len(rows) > 20, 'page': page}
    release_db_session_quietly()
    # 每页最多 20 次本地 stat；不为列表读取任务 JSON，也不跨文件 IO 持有事务。
    for item in payload['items']:
        try:
            root = service.task_root(item['id'])
            name = service.normalize_filename(item['task_title'])
            path = service.safe_file(root, root / ('source' + Path(name).suffix.lower()))
            item['source_bytes'] = path.stat().st_size
        except (OSError, ValueError):
            item['source_bytes'] = None
    return jsonify(success=True, data=payload)


@bp.get('/jobs/<task_id>')
@require_login
def detail(user, task_id):
    row = summaries(user, task_id).first()
    if not row:
        abort(404)
    payload = summary(row)
    if request.args.get('poll') != '1' and payload['status'] == 'completed':
        result = owned(user, task_id).with_entities(PublicationTask.result).scalar() or {}
        payload['outputs'] = {kind: {'bytes': result[kind]['bytes']}
                              for kind in ('text', 'srt') if kind in result}
        payload['segment_count'] = result.get('segment_count')
        payload['duration_seconds'] = result.get('duration_seconds')
    release_db_session_quietly()
    return jsonify(success=True, data=payload)


@bp.get('/jobs/<task_id>/<kind>')
@require_login
def artifact(user, task_id, kind):
    if kind not in ('text', 'srt'):
        abort(404)
    row = owned(user, task_id).with_entities(PublicationTask.status, PublicationTask.result,
                                             PublicationTask.task_title).first()
    if not row:
        abort(404)
    state, result, title = row
    release_db_session_quietly()
    if state != 'completed':
        abort(409)
    try:
        path = service.safe_file(service.task_root(task_id, output=True), result[kind]['path'])
        if not path.is_file() or path.stat().st_size != result[kind]['bytes']:
            abort(404)
    except (KeyError, ValueError):
        abort(404)
    response = send_file(path, mimetype='text/plain; charset=utf-8',
        as_attachment=request.args.get('download') == '1',
        download_name=Path(title).stem + ('.txt' if kind == 'text' else '.srt'))
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@bp.post('/jobs/<task_id>/retry')
@require_login
def retry(user, task_id):
    user = SimpleNamespace(id=str(user.id))
    row = owned(user, task_id).with_entities(PublicationTask.status, PublicationTask.params,
        PublicationTask.celery_task_id, PublicationTask.result, PublicationTask.current_step).first()
    if not row:
        abort(404)
    state, params, previous_id, previous_result, previous_step = row
    release_db_session_quietly()
    if state != 'failed':
        abort(409)
    source = params[service.FEATURE_NAME]
    try:
        path = service.safe_file(service.task_root(task_id), source['source_path'])
        if service.fingerprint(path) != {'bytes': source['source_bytes'], 'sha256': source['source_sha256']}:
            raise ValueError('原始音频已变化，请重新上传')
    except (OSError, ValueError):
        return jsonify(success=False, message='原始音频缺失或已变化，请重新上传'), 400
    delivery_id = str(uuid.uuid4())
    result = dict(previous_result or {})
    attempts = list(result.get('retry_history') or [])[-19:]
    attempts.append({'previous_celery_id': previous_id, 'celery_id': delivery_id,
                     'previous_step': previous_step, 'at': datetime.utcnow().isoformat() + 'Z'})
    result['retry_history'] = attempts
    changed = owned(user, task_id).filter(PublicationTask.status == 'failed',
        PublicationTask.celery_task_id == previous_id).update({
            'status': 'pending', 'current_step': 'queued', 'progress': 0,
            'celery_task_id': delivery_id, 'error_message': None, 'completed_at': None,
            'result': result,
            'updated_at': datetime.utcnow()}, synchronize_session=False)
    db.session.commit()
    release_db_session_quietly()
    if not changed:
        abort(409)
    queued = enqueue(task_id, delivery_id)
    return jsonify(success=queued, data={'id': task_id}, message='已重试' if queued else '入队失败'), 202 if queued else 502
