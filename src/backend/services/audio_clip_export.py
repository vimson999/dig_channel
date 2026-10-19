"""Standalone audio trimming and pitch-preserving MP3 export."""
import math
import re
import subprocess
from pathlib import Path


def _timestamp(value):
    text = str(value).strip()
    if not re.fullmatch(r'\d+(?::\d{1,2}){0,2}(?:\.\d+)?', text):
        raise ValueError('时间请填写秒数、分:秒或时:分:秒')
    parts = [float(part) for part in text.split(':')]
    if any(part >= 60 for part in parts[1:]):
        raise ValueError('时间戳的分、秒必须小于 60')
    result = 0.0
    for part in parts:
        result = result * 60 + part
    if not math.isfinite(result):
        raise ValueError('时间戳无效')
    return result


def clip_options(start='0', end='', speed='1'):
    start = _timestamp(start or '0')
    end = _timestamp(end) if str(end or '').strip() else None
    try:
        speed = float(speed)
    except (ValueError, TypeError) as exc:
        raise ValueError('倍速必须是 0.25 到 4 之间的数字') from exc
    if not math.isfinite(speed) or not 0.25 <= speed <= 4:
        raise ValueError('倍速必须是 0.25 到 4 之间的数字')
    if end is not None and end <= start:
        raise ValueError('截止时间必须晚于开始时间')
    return start, end, speed


def export_audio_clip(source, target, *, start='0', end='', speed='1'):
    start, end, speed = clip_options(start, end, speed)
    duration = float(subprocess.check_output([
        'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=nw=1:nk=1', str(source),
    ], timeout=30))
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError('无法读取音频时长')
    if start >= duration or (end is not None and end > duration + 0.05):
        raise ValueError('起止时间超出音频时长')
    end = min(end, duration) if end is not None else duration
    filters = [f'atrim=duration={end - start}', 'asetpts=PTS-STARTPTS']
    while speed > 2:
        filters.append('atempo=2')
        speed /= 2
    while speed < 0.5:
        filters.append('atempo=0.5')
        speed /= 0.5
    filters.append(f'atempo={speed}')
    subprocess.run([
        'ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
        '-ss', str(start), '-i', str(source),
        '-map', '0:a:0', '-vn', '-af', ','.join(filters),
        '-c:a', 'libmp3lame', '-b:a', '192k', '-threads', '1', str(target),
    ], check=True, capture_output=True, timeout=180)
    if not Path(target).is_file() or Path(target).stat().st_size == 0:
        raise ValueError('音频导出结果为空')
