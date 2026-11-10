"""Reviewable repeated-audio removal workspace."""
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from flask import Blueprint, abort, jsonify, request, send_file
from services.audio_ad_detection import DEFAULT_AD_SAMPLE as SAMPLE, matches
from utils.auth_utils import require_login

bp = Blueprint('audio_ad_removal', __name__, url_prefix='/api/audio-ad-removal')
ROOT = Path(__file__).resolve().parents[4]
WORK = ROOT / 'data/audio-ad-removal/workspaces'
MAX_BYTES = 500 * 1024 * 1024


def workspace(user, job):
    try:
        job = str(uuid.UUID(job))
    except (ValueError, TypeError):
        abort(404)
    path = WORK / str(user.id) / job
    if not path.is_dir():
        abort(404)
    return path


def duration(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                             '-of', 'default=noprint_wrappers=1:nokey=1', str(path)],
                            capture_output=True, text=True, check=True)
    return float(result.stdout.strip())


@bp.get('/default-sample')
@require_login
def default_sample(user):
    return send_file(SAMPLE, mimetype='audio/wav', download_name='default-ad-sample.wav')


@bp.post('/upload')
@require_login
def upload(user):
    source = request.files.get('audio')
    if not source or not source.filename:
        return jsonify(error='请选择讲课音频'), 400
    if request.content_length and request.content_length > MAX_BYTES:
        return jsonify(error='上传文件不能超过 500 MB'), 413
    job = str(uuid.uuid4())
    folder = WORK / str(user.id) / job
    folder.mkdir(parents=True)
    original = folder / 'uploaded_audio'
    path = folder / 'source.mp3'
    source.save(original)
    if original.stat().st_size > MAX_BYTES:
        original.unlink(missing_ok=True)
        return jsonify(error='上传文件不能超过 500 MB'), 413
    try:
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', str(original), '-vn', '-c:a', 'libmp3lame', '-q:a', '2', str(path)], check=True)
        length = duration(path)
    except (subprocess.CalledProcessError, ValueError):
        path.unlink(missing_ok=True)
        return jsonify(error='无法读取音频'), 400
    finally:
        original.unlink(missing_ok=True)
    return jsonify(job=job, duration=length, audio_url=f'/api/audio-ad-removal/{job}/audio')


@bp.get('/<job>/audio')
@require_login
def audio(user, job):
    return send_file(workspace(user, job) / 'source.mp3', mimetype='audio/mpeg', conditional=True)


@bp.post('/<job>/detect')
@require_login
def detect(user, job):
    folder = workspace(user, job)
    sample = request.files.get('sample')
    path = SAMPLE
    if sample and sample.filename:
        path = folder / 'sample'
        sample.save(path)
        if path.stat().st_size > 30 * 1024 * 1024:
            path.unlink(missing_ok=True)
            return jsonify(error='样本不能超过 30 MB'), 413
    try:
        result = matches(folder / 'source.mp3', path, threshold=float(request.form.get('threshold', .55)))
    except (subprocess.CalledProcessError, ValueError):
        return jsonify(error='无法分析音频样本'), 400
    return jsonify(matches=result)


def normalize_ranges(raw, total):
    ranges = sorted((float(r['start']), float(r['end'])) for r in raw)
    if not ranges or any(a < 0 or b > total + .01 or b - a < .05 for a, b in ranges):
        raise ValueError('请选择有效的广告区间')
    if any(ranges[i][0] < ranges[i - 1][1] for i in range(1, len(ranges))):
        raise ValueError('广告区间不能重叠')
    return ranges


def render(source, intervals, destination):
    filters = []
    labels = []
    for i, (a, b) in enumerate(intervals):
        filters.append(f'[0:a]atrim=start={a:.4f}:end={b:.4f},asetpts=PTS-STARTPTS[a{i}]')
        labels.append(f'[a{i}]')
    if len(labels) == 1:
        filters.append(f'{labels[0]}anull[out]')
    else:
        filters.append(''.join(labels) + f'concat=n={len(labels)}:v=0:a=1[out]')
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-i', str(source), '-filter_complex', ';'.join(filters),
                    '-map', '[out]', '-c:a', 'libmp3lame', '-q:a', '2', str(destination)], check=True)


@bp.post('/<job>/export')
@require_login
def export(user, job):
    folder = workspace(user, job)
    try:
        total = duration(folder / 'source.mp3')
        cuts = normalize_ranges(request.get_json()['ranges'], total)
    except (ValueError, KeyError, TypeError):
        return jsonify(error='请先确认有效的广告区间'), 400
    kept, cursor = [], 0.
    for a, b in cuts:
        if a > cursor + .01:
            kept.append((cursor, a))
        cursor = b
    if total > cursor + .01:
        kept.append((cursor, total))
    if not kept:
        return jsonify(error='不能剪掉整段讲课音频'), 400
    filename = '讲课音频-已去广告.mp3'
    output = tempfile.TemporaryFile()
    try:
        with tempfile.TemporaryDirectory() as temp:
            clean = Path(temp) / 'clean.mp3'
            render(folder / 'source.mp3', kept, clean)
            with clean.open('rb') as audio_file:
                shutil.copyfileobj(audio_file, output)
        output.seek(0)
        return send_file(output, mimetype='audio/mpeg', as_attachment=True, download_name=filename)
    except subprocess.CalledProcessError:
        output.close()
        return jsonify(error='音频导出失败'), 500
    except Exception:
        output.close()
        raise
