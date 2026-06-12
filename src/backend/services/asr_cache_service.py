import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
ASR_CACHE_DIR = PROJECT_ROOT / 'data' / 'processed' / 'asr_cache'
ASR_CACHE_AUDIO_DIR = ASR_CACHE_DIR / 'audio'
ASR_SEGMENT_VERSION = 'mlx-whisper-finance-zh-v1'


def _normalize_float(value, default=None):
    if value is None or value == '':
        return default
    return round(float(value), 3)


def _near_integer_alias(value, threshold=0.08):
    if value is None:
        return None
    rounded = round(float(value))
    if abs(float(value) - rounded) <= threshold:
        return float(rounded)
    return None


def probe_media_duration(media_path):
    try:
        result = subprocess.run(
            [
                'ffprobe',
                '-v',
                'error',
                '-show_entries',
                'format=duration',
                '-of',
                'default=noprint_wrappers=1:nokey=1',
                str(media_path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return max(0.0, float(result.stdout.strip()))
    except Exception as exc:
        logger.warning("⚠️ [ASR_CACHE] 无法读取音频时长: %s | %s", media_path, exc)
        return 0.0


def _file_sha256(file_path):
    digest = hashlib.sha256()
    with open(file_path, 'rb') as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def _audio_source_fingerprint(file_path):
    stat = os.stat(file_path)
    return {
        'sha256': _file_sha256(file_path),
        'size': stat.st_size,
    }


def _prompt_fingerprint(initial_prompt):
    if not initial_prompt:
        return None
    return hashlib.sha256(str(initial_prompt).strip().encode('utf-8')).hexdigest()[:16]


def _cache_key(source_path, model_size, start_seconds=None, end_seconds=None, speed=1.0, initial_prompt=None):
    fingerprint = _audio_source_fingerprint(source_path)
    return _cache_key_for_fingerprint(fingerprint, model_size, start_seconds, end_seconds, speed, initial_prompt)


def _cache_key_for_fingerprint(fingerprint, model_size, start_seconds=None, end_seconds=None, speed=1.0, initial_prompt=None):
    payload = {
        'source': fingerprint,
        'start_seconds': _normalize_float(start_seconds, 0.0),
        'end_seconds': _normalize_float(end_seconds),
        'speed': _normalize_float(speed, 1.0),
        'model_size': str(model_size or 'medium'),
        'segment_version': ASR_SEGMENT_VERSION,
    }
    prompt_hash = _prompt_fingerprint(initial_prompt)
    if prompt_hash:
        payload['initial_prompt_hash'] = prompt_hash
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()


def _cache_key_variants(source_path, model_size, start_seconds=None, end_seconds=None, speed=1.0, initial_prompt=None):
    fingerprint = _audio_source_fingerprint(source_path)
    variants = []
    seen = set()

    start_values = [_normalize_float(start_seconds, 0.0)]
    end_values = [_normalize_float(end_seconds)]
    for value in (_near_integer_alias(start_values[0]),):
        if value is not None and value not in start_values:
            start_values.append(value)
    for value in (_near_integer_alias(end_values[0]),):
        if value is not None and value not in end_values:
            end_values.append(value)

    for start_value in start_values:
        for end_value in end_values:
            key = _cache_key_for_fingerprint(
                fingerprint,
                model_size=model_size,
                start_seconds=start_value,
                end_seconds=end_value,
                speed=speed,
                initial_prompt=initial_prompt,
            )
            if key in seen:
                continue
            variants.append(key)
            seen.add(key)
    return variants


def _atempo_filter(speed):
    speed = float(speed or 1.0)
    if abs(speed - 1.0) < 0.001:
        return None
    return f'atempo={speed:.6f}'.rstrip('0').rstrip('.')


def _prepare_audio_for_asr(source_path, output_path, start_seconds=None, end_seconds=None, speed=1.0):
    start_seconds = _normalize_float(start_seconds, 0.0) or 0.0
    end_seconds = _normalize_float(end_seconds)
    speed = _normalize_float(speed, 1.0) or 1.0

    cmd = ['ffmpeg', '-y']
    if start_seconds > 0:
        cmd.extend(['-ss', f'{start_seconds:.3f}'])
    cmd.extend(['-i', str(source_path)])
    if end_seconds is not None:
        trim_duration = max(0.1, end_seconds - start_seconds)
        cmd.extend(['-t', f'{trim_duration:.3f}'])
    audio_filter = _atempo_filter(speed)
    if audio_filter:
        cmd.extend(['-filter:a', audio_filter])
    cmd.extend(['-vn', '-acodec', 'pcm_s16le', '-ar', '44100', '-ac', '2', str(output_path)])

    subprocess.run(
        cmd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=900,
    )


def transcribe_with_cache(
    source_path,
    model_size='medium',
    start_seconds=None,
    end_seconds=None,
    speed=1.0,
    prepared_audio_path=None,
    initial_prompt=None,
    include_metadata=False,
):
    source_path = os.path.abspath(str(source_path))
    if not os.path.exists(source_path):
        raise FileNotFoundError(f'Audio file not found: {source_path}')

    ASR_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    ASR_CACHE_AUDIO_DIR.mkdir(parents=True, exist_ok=True)

    key_variants = _cache_key_variants(
        source_path,
        model_size=model_size,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        speed=speed,
        initial_prompt=initial_prompt,
    )
    key = key_variants[0]
    cache_path = ASR_CACHE_DIR / f'{key}.json'
    for index, candidate_key in enumerate(key_variants):
        candidate_path = ASR_CACHE_DIR / f'{candidate_key}.json'
        if not candidate_path.exists():
            continue
        if index == 0:
            logger.info("⚡️ [ASR_CACHE] 命中字幕缓存: %s", candidate_key[:10])
        else:
            logger.info("⚡️ [ASR_CACHE] 命中字幕缓存别名: %s -> %s", candidate_key[:10], key[:10])
        with open(candidate_path, 'r', encoding='utf-8') as file:
            segments = json.load(file)
        if include_metadata:
            return {
                'segments': segments,
                'metadata': {
                    'cache_hit': True,
                    'cache_key': key,
                    'matched_cache_key': candidate_key,
                    'cache_key_prefix': key[:10],
                    'matched_cache_key_prefix': candidate_key[:10],
                    'model_size': str(model_size or 'medium'),
                    'start_seconds': _normalize_float(start_seconds, 0.0),
                    'end_seconds': _normalize_float(end_seconds),
                    'speed': _normalize_float(speed, 1.0),
                    'initial_prompt_hash': _prompt_fingerprint(initial_prompt),
                    'segment_version': ASR_SEGMENT_VERSION,
                    'cache_path': str(candidate_path),
                },
            }
        return segments

    start_seconds = _normalize_float(start_seconds, 0.0) or 0.0
    end_seconds = _normalize_float(end_seconds)
    speed = _normalize_float(speed, 1.0) or 1.0
    needs_processing = start_seconds > 0 or end_seconds is not None or abs(speed - 1.0) >= 0.001

    transcribe_path = prepared_audio_path if prepared_audio_path and os.path.exists(prepared_audio_path) else None
    if transcribe_path is None:
        if needs_processing:
            transcribe_path = ASR_CACHE_AUDIO_DIR / f'{key}.wav'
            if not transcribe_path.exists():
                logger.info("🎚️ [ASR_CACHE] 生成缓存音频片段: %s", key[:10])
                _prepare_audio_for_asr(source_path, transcribe_path, start_seconds, end_seconds, speed)
        else:
            transcribe_path = source_path

    logger.info(
        "🎙️ [ASR_CACHE] 缓存未命中，开始识别: key=%s model=%s start=%s end=%s speed=%s",
        key[:10],
        model_size,
        start_seconds,
        end_seconds,
        speed,
    )
    from services.asr_service import asr_service

    segments = asr_service.transcribe(
        str(transcribe_path),
        return_segments=True,
        model_size=model_size,
        initial_prompt=initial_prompt,
    )

    fd, temp_path = tempfile.mkstemp(prefix=f'{key}.', suffix='.tmp', dir=str(ASR_CACHE_DIR))
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as file:
            json.dump(segments, file, ensure_ascii=False, indent=2)
        os.replace(temp_path, cache_path)
        for alias_key in key_variants[1:]:
            alias_path = ASR_CACHE_DIR / f'{alias_key}.json'
            if not alias_path.exists():
                try:
                    os.link(cache_path, alias_path)
                except OSError:
                    shutil.copyfile(cache_path, alias_path)
    finally:
        if os.path.exists(temp_path):
            os.remove(temp_path)

    logger.info("✅ [ASR_CACHE] 字幕缓存已写入: %s", key[:10])
    if include_metadata:
        return {
            'segments': segments,
            'metadata': {
                'cache_hit': False,
                'cache_key': key,
                'matched_cache_key': key,
                'cache_key_prefix': key[:10],
                'matched_cache_key_prefix': key[:10],
                'model_size': str(model_size or 'medium'),
                'start_seconds': start_seconds,
                'end_seconds': end_seconds,
                'speed': speed,
                'initial_prompt_hash': _prompt_fingerprint(initial_prompt),
                'segment_version': ASR_SEGMENT_VERSION,
                'cache_path': str(cache_path),
            },
        }
    return segments
