"""Authenticated project-list catalog and administrator JSON upload."""
import json
import logging
from flask import Blueprint, jsonify, request
from models import db
from services.shengcai_project_service import MAX_SOURCE_BYTES, import_catalog, list_catalog
from utils.auth_utils import require_admin, require_login

bp = Blueprint('shengcai_projects', __name__, url_prefix='/api/v1/shengcai-projects')
logger = logging.getLogger(__name__)


@bp.get('')
@require_login
def listing(current_user):
    try:
        result = list_catalog(q=request.args.get('q', ''), platform=request.args.get('platform', ''),
            monetize=request.args.get('monetize', ''), crowd=request.args.get('crowd', ''),
            sort=request.args.get('sort', 'newest'), page=int(request.args.get('page', 1)),
            per_page=int(request.args.get('per_page', 24)),
            secondary_sort=request.args.get('secondary_sort', ''),
            published_within=request.args.get('published_within', 'all'), name=request.args.get('name', ''))
        return jsonify(success=True, data=result)
    except ValueError as exc:
        return jsonify(success=False, error=str(exc)), 400
    except Exception:
        db.session.rollback(); logger.exception('生财项目库读取失败')
        return jsonify(success=False, error='项目库暂时不可用，请稍后重试'), 500


@bp.post('/import')
@require_admin
def upload(current_user):
    try:
        file = request.files.get('file')
        if file is None: raise ValueError('请选择 JSON 文件')
        raw = file.stream.read(MAX_SOURCE_BYTES + 1)
        if len(raw) > MAX_SOURCE_BYTES: raise ValueError('文件不能超过 5 MiB')
        try: source = json.loads(raw.decode('utf-8-sig'))
        except (ValueError, UnicodeError, RecursionError): raise ValueError('文件不是有效的 UTF-8 JSON')
        return jsonify(success=True, data=import_catalog(source, file.filename))
    except ValueError as exc:
        db.session.rollback(); return jsonify(success=False, error=str(exc)), 400
    except Exception:
        db.session.rollback(); logger.exception('生财项目库导入失败')
        return jsonify(success=False, error='导入失败，数据库事务已回滚'), 500
