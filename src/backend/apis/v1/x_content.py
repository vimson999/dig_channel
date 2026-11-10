"""X Growth Studio: authenticated read access, administrator writes."""
import json
from functools import wraps
from flask import Blueprint, jsonify, request, current_app
from sqlalchemy import or_, cast, String, Float, func
from models import db
from models.x_content import MODELS
from services.x_content_service import model_for, serialize, save_record, import_bundle, seed_mock, clear_mock
from utils.auth_utils import require_login, require_admin

bp = Blueprint('x_content', __name__, url_prefix='/api/v1/x-content')

def guarded(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try: return jsonify(success=True, data=fn(*args, **kwargs))
        except ValueError as exc:
            db.session.rollback(); return jsonify(success=False,error=str(exc)),400
        except Exception:
            db.session.rollback(); current_app.logger.exception('X 内容运营请求失败')
            return jsonify(success=False,error='操作失败，请检查服务日志或稍后重试'),500
    return wrapped

@bp.get('/summary')
@require_login
@guarded
def summary(current_user):
    return {'counts':{k:m.query.count() for k,m in MODELS.items()},
            'mock_count':sum(m.query.filter_by(is_mock=True).count() for m in MODELS.values()),
            'can_edit':bool(current_user.is_admin),
            'patterns':[{'id':r.id,'name':r.name} for r in MODELS['patterns'].query.order_by(MODELS['patterns'].name)],
            'cases':[{'id':r.id,'name':(r.author_handle or '')+' · '+r.raw_text[:35]} for r in MODELS['cases'].query.order_by(MODELS['cases'].id)]}

@bp.get('/<kind>')
@require_login
@guarded
def listing(current_user,kind):
    model = model_for(kind); query = model.query
    page = int(request.args.get('page',1)); size = int(request.args.get('per_page',24))
    if page < 1 or not 1 <= size <= 100: raise ValueError('分页参数无效')
    term = request.args.get('q','').strip()
    if term:
        literal = term.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')
        query = query.filter(or_(*[cast(c,String).ilike('%'+literal+'%', escape='\\') for c in model.__table__.columns if isinstance(c.type, (String, db.JSON))]))
    for field in ('time_slot','category','status','evidence_status','pattern_id','action_type'):
        value = request.args.get(field)
        if value:
            if not hasattr(model,field): raise ValueError('该栏目不支持此筛选')
            query = query.filter(getattr(model,field)==value)
    polarity = request.args.get('polarity')
    if polarity:
        if kind != 'cases' or polarity not in ('positive','negative'): raise ValueError('样本筛选无效')
        query = query.filter(model.is_negative_sample == (polarity=='negative'))
    source = request.args.get('source')
    if source:
        if source not in ('mock','real'): raise ValueError('数据来源筛选无效')
        query = query.filter(model.is_mock == (source=='mock'))
    sort = request.args.get('sort','updated_at'); column = model.updated_at
    if sort == 'reply_rate' and hasattr(model,'views'):
        column = cast(model.replies,Float) / func.nullif(model.views,0)
    elif sort == 'follower_leverage' and kind == 'cases':
        column = cast(model.views,Float) / func.nullif(model.author_followers,0)
    elif sort in ('views','priority') and hasattr(model,sort): column = getattr(model,sort)
    elif sort != 'updated_at': raise ValueError('排序参数无效')
    total = query.count()
    rows = query.order_by(column.desc().nullslast(),model.id).offset((page-1)*size).limit(size).all()
    return {'items':[serialize(r) for r in rows], 'total':total,'page':page,'per_page':size}

@bp.get('/<kind>/<ident>')
@require_login
@guarded
def detail(current_user,kind,ident):
    row = db.session.get(model_for(kind),ident)
    if row is None: raise ValueError('记录不存在')
    return serialize(row)

@bp.post('/<kind>')
@require_admin
@guarded
def save(current_user,kind):
    if not request.is_json: raise ValueError('请提交 JSON 对象')
    return serialize(save_record(kind,request.get_json(silent=True)))

@bp.post('/import')
@require_admin
@guarded
def upload(current_user):
    file = request.files.get('file')
    if file is None: raise ValueError('请选择 JSON 文件')
    raw = file.stream.read(5*1024*1024+1)
    if len(raw)>5*1024*1024: raise ValueError('文件不能超过 5 MiB')
    try: bundle=json.loads(raw.decode('utf-8-sig'))
    except (ValueError,UnicodeError,RecursionError): raise ValueError('文件不是有效的 UTF-8 JSON')
    return import_bundle(bundle, replace_mock=request.form.get('replace_mock')=='true')

@bp.post('/mock')
@require_admin
@guarded
def mock(current_user): return seed_mock()

@bp.delete('/mock')
@require_admin
@guarded
def remove_mock(current_user): return clear_mock()
