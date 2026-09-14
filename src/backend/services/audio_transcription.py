"""通用音频转文字的纯文件服务：限量上传、音轨验证、指纹及原子 TXT/SRT 输出。

本模块不访问数据库；调用方应先释放认证或任务查询建立的事务。
"""
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

FEATURE_NAME = 'audio_transcription'
QUEUE_NAME = 'report_audio_workflow'
PROJECT_ROOT = Path(__file__).resolve().parents[3]
EXTENSIONS = {'.mp3', '.m4a', '.wav', '.aac', '.flac', '.ogg', '.opus'}
GENERIC_PROMPT = '请准确转录这段中文音频，使用简体中文，保留原意和自然标点。'


class UploadTooLarge(ValueError):
    """实际上传字节数超限。"""


def max_upload_bytes():
    try:
        value = float(os.getenv('AUDIO_TRANSCRIPTION_MAX_UPLOAD_MB', '2048'))
        if not math.isfinite(value):
            raise ValueError('invalid limit')
    except ValueError:
        value = 2048
    return int(max(1, min(value, 8192)) * 1024 ** 2)


def recognition_prompt(value):
    text = str(value or '').strip()
    if len(text) > 1000:
        raise ValueError('识别提示词不能超过 1000 字')
    return GENERIC_PROMPT + ('\n参考词语：' + text if text else '')


def normalize_filename(value):
    name = str(value or '').replace('\\', '/').split('/')[-1]
    name = ''.join(c for c in name if c >= ' ' and c != '\x7f').strip()[:255]
    if not name or Path(name).suffix.lower() not in EXTENSIONS:
        raise ValueError('请选择 MP3、M4A、WAV、AAC、FLAC、OGG 或 OPUS 音频')
    return name


def task_root(task_id, *, output=False):
    key = str(uuid.UUID(str(task_id)))
    relative = 'outputs/audio_transcription' if output else 'data/uploads/audio_transcription'
    return PROJECT_ROOT / relative / key


def safe_file(root, path):
    root = Path(root).resolve()
    path = Path(path).resolve()
    if not path.is_relative_to(root) or path == root:
        raise ValueError('文件路径不合法')
    return path


def fingerprint(path):
    digest = hashlib.sha256()
    size = 0
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
            size += len(chunk)
    return {'bytes': size, 'sha256': digest.hexdigest()}


def save_upload(stream, path, *, limit=None):
    limit = max_upload_bytes() if limit is None else limit
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    size, digest = 0, hashlib.sha256()
    try:
        with path.open('xb') as target:
            while True:
                chunk = stream.read(min(1024 * 1024, limit - size + 1))
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise UploadTooLarge('音频超过上传大小限制')
                digest.update(chunk)
                target.write(chunk)
            if not size:
                raise ValueError('上传音频为空')
            target.flush()
            os.fsync(target.fileno())
    except FileExistsError:
        raise
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return {'bytes': size, 'sha256': digest.hexdigest()}


def probe_audio(path):
    try:
        result = subprocess.run(
            ['ffprobe', '-v', 'error', '-select_streams', 'a:0', '-show_entries',
             'stream=codec_name:format=duration', '-of', 'json', str(path)],
            capture_output=True, text=True, timeout=30, check=True)
        metadata = json.loads(result.stdout)
        if not metadata.get('streams'):
            raise ValueError('音频不包含可识别的音轨')
        duration = float(metadata.get('format', {}).get('duration') or 0)
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError('无法读取有效音频时长')
        return duration
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError) as exc:
        raise ValueError('音频损坏或无法解析，请检查文件') from exc


def timestamp(seconds):
    millis = max(0, int(float(seconds) * 1000 + 0.5))
    seconds, ms = divmod(millis, 1000)
    minutes, secs = divmod(seconds, 60)
    hours, mins = divmod(minutes, 60)
    return f'{hours:02d}:{mins:02d}:{secs:02d},{ms:03d}'


def write_outputs(root, segments):
    clean = []
    for segment in segments:
        text = ' '.join(str(segment.get('text') or '').split())
        if not text:
            continue
        start, end = float(segment['start']), float(segment['end'])
        if not math.isfinite(start) or not math.isfinite(end):
            raise ValueError('识别结果包含无效时间戳')
        start = max(0, start, clean[-1][1] if clean else 0)
        end = max(start + 0.001, end)
        clean.append((start, end, text))
    if not clean:
        raise ValueError('没有识别到有效语音，请检查音频内容')
    bodies = {
        'text': '\n'.join(item[2] for item in clean) + '\n',
        'srt': ''.join(f'{index}\n{timestamp(start)} --> {timestamp(end)}\n{text}\n\n'
                       for index, (start, end, text) in enumerate(clean, 1)),
    }
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    result = {'segment_count': len(clean)}
    temporary = []
    try:
        for kind, body in bodies.items():
            final = root / ('transcript.txt' if kind == 'text' else 'transcript.srt')
            fd, temp = tempfile.mkstemp(prefix='.transcript-', dir=root)
            temporary.append((temp, final))
            with os.fdopen(fd, 'w', encoding='utf-8', newline='\n') as stream:
                stream.write(body)
                stream.flush()
                os.fsync(stream.fileno())
            result[kind] = {'path': str(final), 'bytes': len(body.encode('utf-8'))}
        for temp, final in temporary:
            os.replace(temp, final)
    finally:
        for temp, _ in temporary:
            Path(temp).unlink(missing_ok=True)
    return result
