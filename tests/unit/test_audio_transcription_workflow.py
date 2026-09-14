"""转写 API/消费者集成验证，使用独立 SQLite 与真实音频文件。"""
import io
import fcntl
import sys
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import Flask
from sqlalchemy import event, text
from sqlalchemy.exc import OperationalError
from celery.exceptions import Retry
from werkzeug.exceptions import NotFound, Conflict

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/backend'))
from models import db
from models.task import PublicationTask
from api import audio_transcription as api
from tasks import audio_transcription as worker
from services import audio_transcription as service


@pytest.fixture
def context(tmp_path, monkeypatch):
    app = Flask(__name__)
    app.config.update(SQLALCHEMY_DATABASE_URI='sqlite://', SQLALCHEMY_TRACK_MODIFICATIONS=False)
    db.init_app(app)
    monkeypatch.setattr(service, 'PROJECT_ROOT', tmp_path)
    with app.app_context():
        PublicationTask.__table__.create(db.engine)
        yield app
        db.session.remove()
        PublicationTask.__table__.drop(db.engine)


def wav():
    output = io.BytesIO()
    with wave.open(output, 'wb') as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(b'\0\0' * 16000)
    output.seek(0)
    return output


def create(app):
    def publish(*args, **kwargs):
        assert not db.session().in_transaction()
        return {'async_result': SimpleNamespace(id=kwargs['broker_task_id'])}
    with patch.object(api, 'maybe_guarded_apply_async', side_effect=publish):
        with app.test_request_context(method='POST', data={'audio': (wav(), '会议.wav')}):
            # 模拟认证查询已开启的只读事务。
            db.session.execute(text('select 1'))
            response, status = api.create.__wrapped__(SimpleNamespace(id='owner'))
    assert status == 202
    return response.get_json()['data']['id']


def test_upload_execute_download_and_user_isolation(context):
    task_id = create(context)
    delivery = db.session.query(PublicationTask.celery_task_id).filter_by(id=task_id).scalar()
    db.session.remove()
    def transcribe(*args, **kwargs):
        assert not db.session().in_transaction()
        assert '财经' not in kwargs['initial_prompt']
        return {'segments': [{'start': 0, 'end': 1, 'text': '会议开始。'}], 'metadata': {'cache_hit': False}}
    with patch('services.asr_cache_service.transcribe_with_cache', side_effect=transcribe) as recognize:
        assert worker.execute(task_id, delivery)
        assert not worker.execute(task_id, delivery)
        assert recognize.call_count == 1
    for kind in ('text', 'srt'):
        with context.test_request_context():
            response = api.artifact.__wrapped__(SimpleNamespace(id='owner'), task_id, kind)
            response.direct_passthrough = False
            assert '会议开始。' in response.get_data(as_text=True)
            response.close()
        with context.test_request_context(), pytest.raises(NotFound):
            api.artifact.__wrapped__(SimpleNamespace(id='other'), task_id, kind)
    with context.test_request_context():
        detail = api.detail.__wrapped__(SimpleNamespace(id='owner'), task_id).get_json()['data']
    assert detail['outputs']['text']['bytes'] == 16
    assert detail['outputs']['srt']['bytes'] > 16
    assert 'path' not in str(detail)
    with context.test_request_context('/?poll=1'):
        polling = api.detail.__wrapped__(SimpleNamespace(id='owner'), task_id).get_json()['data']
    assert 'outputs' not in polling


def test_poll_uses_only_narrow_columns(context):
    task_id = create(context)
    statements = []
    def capture(conn, cursor, statement, parameters, ctx, many):
        statements.append(statement.lower())
    event.listen(db.engine, 'before_cursor_execute', capture)
    try:
        with context.test_request_context():
            response = api.detail.__wrapped__(SimpleNamespace(id='owner'), task_id)
        assert response.get_json()['data']['id'] == task_id
        assert all('publication_tasks.params' not in sql and 'publication_tasks.result' not in sql for sql in statements)
    finally:
        event.remove(db.engine, 'before_cursor_execute', capture)


def test_history_contains_size_without_payload_queries(context):
    task_id = create(context)
    with context.test_request_context():
        payload = api.history.__wrapped__(SimpleNamespace(id='owner')).get_json()['data']
    assert payload['items'][0]['id'] == task_id
    assert payload['items'][0]['source_bytes'] == 32044
    assert payload['items'][0]['progress'] == 0
    with context.test_request_context():
        assert api.history.__wrapped__(SimpleNamespace(id='other')).get_json()['data']['items'] == []


@pytest.mark.parametrize('files', [[], [('a.wav', b'')], [('a.exe', b'x')], [('a.wav', b'bad')], [('a.wav', b'x'), ('b.wav', b'y')]])
def test_invalid_uploads_leave_no_jobs(context, files):
    with context.test_request_context(method='POST', data={'audio': [(io.BytesIO(data), name) for name, data in files]}):
        _, status = api.create.__wrapped__(SimpleNamespace(id='owner'))
    assert status == 400
    assert db.session.query(PublicationTask.id).count() == 0


def test_broker_failure_persists_failed_record(context):
    with patch.object(api, 'maybe_guarded_apply_async', side_effect=RuntimeError('broker down')):
        with context.test_request_context(method='POST', data={'audio': (wav(), '会议.wav')}):
            response, status = api.create.__wrapped__(SimpleNamespace(id='owner'))
    assert status == 502
    row = db.session.query(PublicationTask.status, PublicationTask.current_step).filter_by(id=response.get_json()['data']['id']).one()
    assert row.status == 'failed'
    assert row.current_step == 'enqueue_failed'


def test_concurrent_delivery_does_not_start_second_recognition(context):
    task_id = create(context)
    delivery = db.session.query(PublicationTask.celery_task_id).filter_by(id=task_id).scalar()
    db.session.remove()
    root = service.task_root(task_id, output=True)
    root.mkdir(parents=True)
    with (root / '.execution.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with patch('services.asr_cache_service.transcribe_with_cache') as recognize:
            assert not worker.execute(task_id, delivery)
            recognize.assert_not_called()
    assert db.session.query(PublicationTask.status).filter_by(id=task_id).scalar() == 'pending'


def test_interrupted_processing_delivery_resumes(context):
    task_id = create(context)
    delivery = db.session.query(PublicationTask.celery_task_id).filter_by(id=task_id).scalar()
    PublicationTask.query.filter_by(id=task_id).update({'status': 'processing', 'current_step': 'transcribing'})
    db.session.commit()
    db.session.remove()
    with patch('services.asr_cache_service.transcribe_with_cache', return_value={
        'segments': [{'start': 0, 'end': 1, 'text': '恢复完成。'}], 'metadata': {'cache_hit': True}}):
        assert worker.execute(task_id, delivery)
    row = db.session.query(PublicationTask.status, PublicationTask.result).filter_by(id=task_id).one()
    assert row.status == 'completed'
    assert row.result['cache_hit'] is True


def test_oversized_declared_request_rejected_before_body(context):
    with context.test_request_context(method='POST', environ_overrides={'CONTENT_LENGTH': str(2 * 1024 ** 3 + 2 * 1024 ** 2)}):
        _, status = api.create.__wrapped__(SimpleNamespace(id='owner'))
    assert status == 413
    assert db.session.query(PublicationTask.id).count() == 0


def test_asr_failure_is_retryable_and_old_delivery_rejected(context):
    task_id = create(context)
    delivery = db.session.query(PublicationTask.celery_task_id).filter_by(id=task_id).scalar()
    db.session.remove()
    with patch('services.asr_cache_service.transcribe_with_cache', side_effect=RuntimeError('private path')):
        assert not worker.execute(task_id, delivery)
    row = db.session.query(PublicationTask.status, PublicationTask.error_message).filter_by(id=task_id).one()
    assert row.status == 'failed'
    assert 'private path' not in row.error_message
    db.session.remove()
    with patch.object(api, 'maybe_guarded_apply_async', return_value={}):
        with context.test_request_context(method='POST'):
            _, status = api.retry.__wrapped__(SimpleNamespace(id='owner'), task_id)
    assert status == 202
    assert not worker.execute(task_id, delivery)
    fresh = db.session.query(PublicationTask.result, PublicationTask.celery_task_id).filter_by(id=task_id).one()
    assert fresh.result['retry_history'][-1]['previous_celery_id'] == delivery
    assert fresh.result['retry_history'][-1]['celery_id'] == fresh.celery_task_id


@pytest.mark.parametrize('failure', ['empty', 'write'])
def test_failed_outputs_are_not_downloadable(context, failure):
    task_id = create(context)
    delivery = db.session.query(PublicationTask.celery_task_id).filter_by(id=task_id).scalar()
    db.session.remove()
    segments = [] if failure == 'empty' else [{'start': 0, 'end': 1, 'text': '测试'}]
    with patch('services.asr_cache_service.transcribe_with_cache', return_value={'segments': segments}):
        if failure == 'write':
            with patch.object(service.os, 'replace', side_effect=OSError('disk full')):
                assert not worker.execute(task_id, delivery)
        else:
            assert not worker.execute(task_id, delivery)
    assert db.session.query(PublicationTask.status).filter_by(id=task_id).scalar() == 'failed'
    for kind in ('text', 'srt'):
        with context.test_request_context(), pytest.raises(Conflict):
            api.artifact.__wrapped__(SimpleNamespace(id='owner'), task_id, kind)


def test_all_routes_require_login(context):
    context.register_blueprint(api.bp)
    with patch('utils.auth_utils.get_current_user', return_value=None):
        client = context.test_client()
        for method, url in [('GET', '/status'), ('GET', '/jobs'), ('POST', '/jobs'),
                            ('GET', '/jobs/unknown'), ('GET', '/jobs/unknown/text'),
                            ('GET', '/jobs/unknown/srt'), ('POST', '/jobs/unknown/retry')]:
            assert client.open('/api/audio-transcriptions' + url, method=method).status_code == 401


def test_cross_user_detail_retry_and_active_retry_are_rejected(context):
    task_id = create(context)
    with context.test_request_context(), pytest.raises(NotFound):
        api.detail.__wrapped__(SimpleNamespace(id='other'), task_id)
    with context.test_request_context(method='POST'), pytest.raises(NotFound):
        api.retry.__wrapped__(SimpleNamespace(id='other'), task_id)
    with context.test_request_context(method='POST'), pytest.raises(Conflict):
        api.retry.__wrapped__(SimpleNamespace(id='owner'), task_id)


@pytest.mark.parametrize('boundary', ['initial_read', 'failure_write'])
def test_database_outage_retries_same_delivery_then_completes(context, boundary):
    task_id = create(context)
    delivery = db.session.query(PublicationTask.celery_task_id).filter_by(id=task_id).scalar()
    db.session.remove()
    outage = OperationalError('select', {}, RuntimeError('connection refused'))
    real_update = worker.update
    def failure_write(*args, **kwargs):
        if kwargs.get('status') == 'failed':
            raise outage
        return real_update(*args, **kwargs)
    worker.process_job.push_request(id=delivery, retries=0)
    try:
        with patch('app.create_app', return_value=context), patch.object(worker, 'guard_publication_task_start'), \
             patch.object(worker.process_job, 'retry', side_effect=Retry()) as retry:
            with pytest.raises(Retry):
                if boundary == 'initial_read':
                    with patch.object(db.session, 'query', side_effect=outage):
                        worker.process_job.run(task_id)
                else:
                    with patch.object(worker, 'update', side_effect=failure_write), \
                         patch('services.asr_cache_service.transcribe_with_cache', side_effect=ValueError('ASR failure')):
                        worker.process_job.run(task_id)
            assert retry.call_args.kwargs['countdown'] == 15
        assert db.session.query(PublicationTask.status).filter_by(id=task_id).scalar() in ('pending', 'processing')
        db.session.remove()
        with patch('app.create_app', return_value=context), patch.object(worker, 'guard_publication_task_start'), \
             patch('services.asr_cache_service.transcribe_with_cache', return_value={
                 'segments': [{'start': 0, 'end': 1, 'text': '数据库已恢复。'}]}):
            assert worker.process_job.run(task_id)
        assert db.session.query(PublicationTask.status).filter_by(id=task_id).scalar() == 'completed'
    finally:
        worker.process_job.pop_request()


def test_database_retry_budget_settles_failed_without_running_asr(context):
    task_id = create(context)
    delivery = db.session.query(PublicationTask.celery_task_id).filter_by(id=task_id).scalar()
    db.session.remove()
    worker.process_job.push_request(id=delivery, retries=5)
    try:
        with patch('app.create_app', return_value=context), patch.object(worker, 'execute') as execute:
            assert worker.process_job.run(task_id, db_retry_count=5) is False
            execute.assert_not_called()
        row = db.session.query(PublicationTask.status, PublicationTask.error_message).filter_by(id=task_id).one()
        assert row.status == 'failed'
        assert '数据库' in row.error_message
    finally:
        worker.process_job.pop_request()


def test_real_celery_retry_preserves_delivery_after_budget_during_db_outage(context):
    task_id = create(context)
    delivery = db.session.query(PublicationTask.celery_task_id).filter_by(id=task_id).scalar()
    db.session.remove()
    worker.process_job.push_request(id=delivery, retries=8, called_directly=False, is_eager=False,
                                   args=[task_id], kwargs={'db_retry_count': 5})
    try:
        with patch('app.create_app', return_value=context), \
             patch.object(db.session, 'query', side_effect=OperationalError('select', {}, RuntimeError('offline'))), \
             patch('celery.canvas.Signature.apply_async'):
            with pytest.raises(Retry) as retried:
                worker.process_job.run(task_id, db_retry_count=5)
        assert retried.value.when == 300
        assert retried.value.sig.options['task_id'] == delivery
        assert retried.value.sig.kwargs == {'db_retry_count': 5}
    finally:
        worker.process_job.pop_request()
