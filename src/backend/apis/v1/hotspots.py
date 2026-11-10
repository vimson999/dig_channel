"""Agent ingestion and administrator review of hotspot candidates."""
import base64
import binascii
import hmac
from io import BytesIO

from PIL import Image
import json
import os
import re
from datetime import datetime, timezone
from time import monotonic
from urllib.parse import urlsplit
from uuid import uuid4

from flask import Blueprint, current_app, g, jsonify, request, send_file, url_for
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge

from models import db
from models.hotspot import Hotspot, XPost
from utils.auth_utils import require_admin

bp = Blueprint('hotspots', __name__, url_prefix='/api/v1')
SYNC_PATH = '/api/v1/sync/hotspots-with-posts'
HOT_STATUSES = ('pending', 'processed', 'ignored')
POST_STATUSES = ('draft', 'approved', 'published', 'rejected')
MAX_BODY = 5 * 1024 * 1024
POST_TYPES = ('short_tweet', 'thread', 'card_text')


def field(data, key, limit, required=False):
    value = data.get(key)
    if value is None and not required:
        return None
    if not isinstance(value, str) or len(value) > limit or '\x00' in value or (required and not value.strip()):
        raise ValueError(f'{key}: invalid text')
    return value


def identifier(data):
    value = field(data, 'id', 64, True)
    if not re.fullmatch(r'[A-Za-z0-9_-]+', value):
        raise ValueError('id: invalid format')
    return value


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.utcoffset() is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OverflowError):
        raise ValueError('timestamp must be ISO 8601 with timezone') from None


def tags(data):
    values = data.get('suggested_tags', [])
    if not isinstance(values, list) or len(values) > 30 or any(
        not isinstance(v, str) or not v.strip() or len(v) > 64 or '\x00' in v for v in values
    ):
        raise ValueError('suggested_tags: invalid list')
    return values


def post_text(data):
    return dict(content=field(data, 'content', 50000, True), hook=field(data, 'hook', 255),
                call_to_action=field(data, 'call_to_action', 255), suggested_tags=tags(data))


def image_fields(hotspot):
    fields = {}
    cover = field(hotspot, 'cover_image_url', 512)
    if cover is not None:
        parsed = urlsplit(cover)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username is not None or any(c.isspace() for c in cover)):
            raise ValueError('cover_image_url: invalid HTTP URL')
        fields['cover_image_url'] = cover
    evidence = field(hotspot, 'evidence_image_base64', MAX_BODY)
    if evidence is not None:
        header, separator, encoded = evidence.partition(',')
        formats = {'data:image/png;base64': 'PNG', 'data:image/jpeg;base64': 'JPEG'}
        if not separator or header not in formats:
            raise ValueError('evidence_image_base64: expected PNG or JPEG data URL')
        try:
            binary = base64.b64decode(encoded, validate=True)
            with Image.open(BytesIO(binary)) as image:
                if image.format != formats[header] or image.width * image.height > 25_000_000:
                    raise ValueError()
                image.verify()
            with Image.open(BytesIO(binary)) as image:
                image.load()
        except (binascii.Error, OSError, ValueError, SyntaxError, Image.DecompressionBombError):
            raise ValueError('evidence_image_base64: invalid image or exceeds 25 megapixels') from None
        fields.update(evidence_image=binary, evidence_image_type=header.split(':')[1].split(';')[0])
    return fields


def parse_bundle(bundle):
    if not isinstance(bundle, dict) or not isinstance(bundle.get('hotspot'), dict):
        raise ValueError('hotspot must be an object')
    hotspot, posts = bundle['hotspot'], bundle.get('posts')
    if not isinstance(posts, list) or not 1 <= len(posts) <= 100:
        raise ValueError('posts must contain 1–100 candidates')
    rank = hotspot.get('rank')
    if rank is not None and (type(rank) is not int or not 1 <= rank <= 2147483647):
        raise ValueError('rank: invalid positive integer')
    url = field(hotspot, 'raw_url', 512)
    if url:
        parsed = urlsplit(url)
        if parsed.scheme not in ('https', 'http') or not parsed.hostname or any(c.isspace() for c in url):
            raise ValueError('raw_url: invalid HTTP URL')
    hot = dict(id=identifier(hotspot), source_platform=field(hotspot, 'source_platform', 32, True),
               category=field(hotspot, 'category', 32, True), title=field(hotspot, 'title', 255, True),
               rank=rank, heat_value=field(hotspot, 'heat_value', 64), raw_url=url,
               summary=field(hotspot, 'summary', 20000), scraped_at=timestamp(hotspot.get('scraped_at')),
               status='pending', **image_fields(hotspot))
    candidates, seen = [], set()
    for post in posts:
        if not isinstance(post, dict):
            raise ValueError('post must be an object')
        ident = identifier(post)
        if ident in seen:
            raise ValueError('duplicate post id')
        seen.add(ident)
        post_type = field(post, 'post_type', 32, True)
        if post_type not in POST_TYPES:
            raise ValueError('invalid post_type')
        candidates.append(dict(id=ident, hotspot_id=hot['id'], persona=field(post, 'persona', 64, True),
                               post_type=post_type, publish_status='draft', **post_text(post),
                               created_at=timestamp(post['created_at']) if 'created_at' in post else
                               datetime.now(timezone.utc).replace(tzinfo=None)))
    return hot, candidates


class CandidateConflict(Exception):
    pass


def save_bundle(hot, candidates):
    insert = sqlite_insert if db.engine.dialect.name == 'sqlite' else pg_insert
    statement = insert(Hotspot).values(hot)
    updates = {key: statement.excluded[key] for key in
               ('evidence_image', 'evidence_image_type', 'cover_image_url') if key in hot}
    db.session.execute(statement.on_conflict_do_update(index_elements=['id'], set_=updates)
                       if updates else statement.on_conflict_do_nothing(index_elements=['id']))
    saved = 0
    for candidate in sorted(candidates, key=lambda row: row['id']):
        result = db.session.execute(insert(XPost).values(candidate).on_conflict_do_nothing(index_elements=['id']))
        saved += result.rowcount
        if not result.rowcount and db.session.get(XPost, candidate['id']).hotspot_id != hot['id']:
            raise CandidateConflict()
    db.session.commit()
    return saved


@bp.post('/sync/hotspots-with-posts')
def ingest():
    started = monotonic()
    request_id = getattr(g, 'request_id', uuid4().hex)
    hot_id, submitted, saved = None, 0, 0

    def respond(status, message, data=None):
        current_app.logger.log(
            20 if status == 200 else 30,
            'hotspot_sync request_id=%s caller=hotspot-agent hotspot_id=%s submitted=%d saved=%d status=%d reason=%s duration_ms=%d',
            request_id, hot_id, submitted, saved, status, message, (monotonic() - started) * 1000,
        )
        payload = dict(code=0 if status == 200 else status, message=message, request_id=request_id)
        if data is not None:
            payload['data'] = data
        return jsonify(payload), status, {'X-Request-ID': request_id}

    expected = os.getenv('HOTSPOT_SYNC_TOKEN', '').strip()
    supplied = request.headers.get('Authorization', '').partition(' ')
    if len(expected) < 32:
        return respond(503, 'hotspot sync token is not configured')
    if supplied[0].lower() != 'bearer' or not hmac.compare_digest(supplied[2].encode(), expected.encode()):
        return respond(401, 'invalid bearer token')
    if not request.is_json:
        return respond(415, 'expected application/json')
    try:
        raw = request.stream.read(MAX_BODY + 1)
        if len(raw) > MAX_BODY:
            return respond(413, 'payload exceeds 5 MiB')
        hot, candidates = parse_bundle(json.loads(raw))
        hot_id, submitted = hot['id'], len(candidates)
        saved = save_bundle(hot, candidates)
    except RequestEntityTooLarge:
        return respond(413, 'payload exceeds 5 MiB')
    except (BadRequest, RecursionError, json.JSONDecodeError, UnicodeError):
        return respond(400, 'invalid JSON')
    except ValueError as error:
        return respond(400, str(error) if type(error) is ValueError and not isinstance(error, UnicodeError) else 'invalid input')
    except CandidateConflict:
        db.session.rollback()
        return respond(409, 'post id belongs to another hotspot')
    except Exception as error:
        db.session.rollback()
        current_app.logger.error('hotspot_sync request_id=%s error_type=%s', request_id, type(error).__name__)
        return respond(500, 'database write failed')
    return respond(200, 'success', dict(hotspot_id=hot_id, posts_saved_count=saved,
                                        posts_existing_count=submitted - saved))


def record(row):
    return {col.name: value.isoformat() + 'Z' if isinstance(value, datetime) else value
            for col in row.__table__.columns if col.name not in ('evidence_image', 'evidence_image_type')
            for value in [getattr(row, col.name)]}


def hotspot_record(row):
    return dict(record(row), evidence_image_url=url_for('hotspots.evidence_image', ident=row.id)
                if row.evidence_image_type else None)


@bp.get('/hotspots/<ident>/evidence-image')
@require_admin
def evidence_image(current_user, ident):
    image = db.session.query(Hotspot.evidence_image, Hotspot.evidence_image_type).filter_by(id=ident).first()
    if image is None or not image.evidence_image:
        from flask import abort
        abort(404)
    extension = 'png' if image.evidence_image_type == 'image/png' else 'jpg'
    response = send_file(BytesIO(image.evidence_image), mimetype=image.evidence_image_type,
                         download_name=f'{ident}.{extension}', max_age=0)
    response.headers['Cache-Control'] = 'private, no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


@bp.get('/hotspots')
@require_admin
def list_hotspots(current_user):
    query = Hotspot.query
    for name in ('source_platform', 'category', 'status'):
        if request.args.get(name):
            query = query.filter(getattr(Hotspot, name) == request.args[name])
    if request.args.get('q'):
        query = query.filter(Hotspot.title.icontains(request.args['q'][:255], autoescape=True))
    page = max(1, request.args.get('page', 1, type=int))
    result = query.order_by(Hotspot.scraped_at.desc(), Hotspot.id).paginate(page=page, per_page=30, error_out=False)
    return jsonify(data=[hotspot_record(row) for row in result.items], has_next=result.has_next)


@bp.get('/hotspots/<ident>')
@require_admin
def detail(current_user, ident):
    hotspot = db.get_or_404(Hotspot, ident)
    posts = XPost.query.filter_by(hotspot_id=ident).order_by(XPost.created_at, XPost.id)
    return jsonify(data=dict(hotspot=hotspot_record(hotspot), posts=[record(post) for post in posts]))


@bp.patch('/hotspots/<ident>')
@require_admin
def review_hotspot(current_user, ident):
    hotspot = db.get_or_404(Hotspot, ident)
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or data.get('status') not in HOT_STATUSES:
        return jsonify(message='invalid hotspot status'), 400
    hotspot.status = data['status']
    db.session.commit()
    return jsonify(data=hotspot_record(hotspot))


@bp.patch('/hotspots/<ident>/posts/<post_id>')
@require_admin
def review_post(current_user, ident, post_id):
    post = XPost.query.filter_by(id=post_id, hotspot_id=ident).first_or_404()
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not data or set(data) - {'content', 'hook', 'call_to_action', 'suggested_tags', 'publish_status'}:
        return jsonify(message='invalid update fields'), 400
    try:
        if 'publish_status' in data and data['publish_status'] not in POST_STATUSES:
            raise ValueError('invalid publish status')
        values = post_text({**record(post), **data})
    except ValueError as error:
        return jsonify(message=str(error)), 400
    for key in data:
        setattr(post, key, data[key] if key == 'publish_status' else values[key])
    db.session.commit()
    return jsonify(data=record(post))
