"""Administrator upload, preview, commit and history for offline API captures."""
import hashlib
import json
import logging
from flask import Blueprint, jsonify, request
from models import db
from models.zsxq_sync import ZsxqSyncRun
from services.zsxq_json_import_service import MAX_SOURCE_BYTES, preview_capture, import_capture
from utils.auth_utils import require_admin

bp = Blueprint('zsxq_json_import', __name__, url_prefix='/api/v1/zsxq/json-import')
logger = logging.getLogger(__name__)


def _upload():
    upload = request.files.get('file')
    if upload is None:
        raise ValueError('请选择 JSON 文件')
    raw = upload.stream.read(MAX_SOURCE_BYTES + 1)
    if len(raw) > MAX_SOURCE_BYTES:
        raise ValueError('JSON 文件不能超过 12 MiB')
    try:
        source = json.loads(raw.decode('utf-8-sig'))
    except (UnicodeError, ValueError, RecursionError):
        raise ValueError('文件不是有效的 UTF-8 JSON')
    return source, hashlib.sha256(raw).hexdigest()


@bp.post('/preview')
@require_admin
def preview(current_user):
    try:
        source, digest = _upload()
        result = preview_capture(source); result['file_sha256'] = digest
        return jsonify(success=True, data=result)
    except ValueError as exc:
        db.session.rollback()
        return jsonify(success=False, error=str(exc)), 400
    except Exception:
        db.session.rollback(); logger.exception('JSON 导入预览失败')
        return jsonify(success=False, error='预览失败，请检查日志'), 500


@bp.post('/commit')
@require_admin
def commit(current_user):
    try:
        source, digest = _upload()
        if request.form.get('file_sha256') != digest:
            raise ValueError('文件已改变或尚未预览，请重新预览')
        result = import_capture(source)
        return jsonify(success=True, data=result)
    except ValueError as exc:
        db.session.rollback()
        return jsonify(success=False, error=str(exc)), 400
    except Exception:
        db.session.rollback(); logger.exception('JSON 导入失败')
        return jsonify(success=False, error='导入失败，数据库事务已回滚，请检查日志'), 500


@bp.get('/history')
@require_admin
def history(current_user):
    runs = ZsxqSyncRun.query.filter_by(mode='json_import').order_by(ZsxqSyncRun.created_at.desc()).limit(20).all()
    return jsonify(success=True, data=[dict(r.result or {}, run_id=r.id, group_name=r.group_name,
                                          status=r.status) for r in runs])
