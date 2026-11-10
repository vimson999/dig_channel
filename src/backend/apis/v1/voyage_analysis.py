"""Read-only API for the independent voyage analysis database table."""
from sqlalchemy import text

from flask import Blueprint, current_app, jsonify

from models import db
from models.voyage_analysis import VoyageAnalysis
from utils.auth_utils import require_login


bp = Blueprint('voyage_analysis', __name__, url_prefix='/api/v1/voyage-analysis')


def _ratings():
    rows = db.session.execute(text('SELECT analysis_id, score, grade, reason FROM voyage_analysis_ratings')).mappings()
    return {row['analysis_id']: {'score': row['score'], 'grade': row['grade'], 'reason': row['reason']} for row in rows}


def _metadata(row, ratings):
    return {
        'id': row.id,
        'rating': ratings.get(row.id),
        'activity_id': row.activity_id,
        'title': row.title,
        'analysis_date': row.analysis_date.isoformat() if row.analysis_date else '',
        'status': row.status,
        'summary': row.summary,
    }


@bp.get('')
@require_login
def listing(current_user):
    try:
        ratings = _ratings()
        rows = VoyageAnalysis.query.order_by(VoyageAnalysis.sort_order.asc(), VoyageAnalysis.id.asc()).all()
        rows.sort(key=lambda row: -(ratings.get(row.id) or {}).get('score', -1))
        return jsonify(success=True, data={'items': [_metadata(row, ratings) for row in rows], 'total': len(rows)})
    except Exception:
        db.session.rollback()
        current_app.logger.exception('读取航海项目分析目录失败')
        return jsonify(success=False, error='航海项目分析暂时不可用'), 500


@bp.get('/<slug>')
@require_login
def detail(current_user, slug):
    try:
        row = db.session.get(VoyageAnalysis, slug)
        if row is None:
            return jsonify(success=False, error='没有找到该项目分析'), 404
        return jsonify(success=True, data={**_metadata(row, _ratings()), 'content': row.content_markdown})
    except Exception:
        db.session.rollback()
        current_app.logger.exception('读取航海项目分析正文失败')
        return jsonify(success=False, error='项目分析正文暂时不可用'), 500
