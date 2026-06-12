"""
文档转换API接口
支持Markdown和Word文档的相互转换
"""

import os
import hashlib
import json
import math
import re
import platform
import shutil
import subprocess
import tempfile
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path
from flask import Blueprint, request, jsonify, send_file
from werkzeug.utils import secure_filename
import logging

# 创建蓝图
bp = Blueprint('documents', __name__, url_prefix='/api/v1/documents')

# 配置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 支持的文件格式
ALLOWED_EXTENSIONS = {
    'markdown': ['.md', '.markdown', '.txt'],
    'docx': ['.docx', '.doc']
}

# 文件大小限制 (10MB)
MAX_FILE_SIZE = 10 * 1024 * 1024
LONG_IMAGE_MAX_FILE_SIZE = 50 * 1024 * 1024
PDF_VIDEO_MAX_FILE_SIZE = 80 * 1024 * 1024
PODCAST_AUDIO_MAX_FILE_SIZE = 300 * 1024 * 1024
PODCAST_TEMPLATE_MAX_FILE_SIZE = 30 * 1024 * 1024
LONG_IMAGE_MAX_PAGES = 80
LONG_IMAGE_MAX_PIXELS = 180_000_000
PODCAST_AUDIO_EXTENSIONS = {'.mp3', '.m4a', '.wav', '.aac', '.flac', '.ogg'}
PODCAST_TEMPLATE_PATH = (
    Path(__file__).resolve().parents[3]
    / 'frontend'
    / 'assets'
    / 'podcast'
    / 'modern_minimalist_podcast_player.png'
)
PODCAST_VISUALIZER_RENDERER_VERSION = '20260520-audio-controls-v9'
PODCAST_PREVIEW_CACHE_DIR = Path(tempfile.gettempdir()) / 'shioa_podcast_preview_cache'
PROJECT_ROOT = Path(__file__).resolve().parents[4]

def _is_podcast_visualizer_task(task):
    params = task.params if isinstance(task.params, dict) else {}
    return params.get('feature') == 'podcast_visualizer'

def _enqueue_podcast_visualizer_task(task):
    from models import db
    from tasks.podcast_visualizer import run_podcast_visualizer_task

    try:
        celery_task = run_podcast_visualizer_task.apply_async(
            args=[task.id],
            queue='podcast_visualizer',
            retry=False,
        )
    except Exception as exc:
        logger.exception("Podcast visualizer task enqueue failed: %s", task.id)
        task.status = 'failed'
        task.current_step = 'failed'
        task.progress = 0
        task.error_message = f'播客任务入队失败: {exc}'
        task.completed_at = datetime.utcnow()
        db.session.commit()
        raise

    task.celery_task_id = celery_task.id
    task.status = 'pending'
    task.current_step = 'init'
    task.progress = 0
    task.error_message = None
    task.completed_at = None
    db.session.commit()
    return celery_task

def allowed_file(filename, source_format):
    """检查文件扩展名是否被允许"""
    if not filename or '.' not in filename:
        return False
    
    ext = os.path.splitext(filename.lower())[1]
    return ext in ALLOWED_EXTENSIONS.get(source_format, [])

def get_file_size_str(size_bytes):
    """将字节数转换为可读的文件大小字符串"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"

def _safe_base_name(filename):
    """保留原始中文名作为下载文件名，路径落盘仍使用安全名。"""
    base_name = os.path.splitext(os.path.basename(filename or 'document'))[0].strip()
    return base_name or 'document'

def _find_soffice():
    candidates = [
        shutil.which('soffice'),
        shutil.which('libreoffice'),
        '/Applications/LibreOffice.app/Contents/MacOS/soffice',
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return None

def _convert_word_to_pdf(input_path, output_dir):
    """优先使用 LibreOffice；macOS 本机没有时回退到 Microsoft Word。"""
    soffice = _find_soffice()
    if soffice:
        subprocess.run(
            [
                soffice,
                '--headless',
                '--convert-to',
                'pdf',
                '--outdir',
                output_dir,
                input_path,
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=180,
        )
        output_path = os.path.join(output_dir, f"{Path(input_path).stem}.pdf")
        if os.path.exists(output_path):
            return output_path

    if os.path.exists('/Applications/Microsoft Word.app'):
        output_path = os.path.join(output_dir, f"{Path(input_path).stem}.pdf")
        script = f'''
        set inputPath to POSIX file "{input_path}" as alias
        set outputPath to POSIX file "{output_path}"
        tell application "Microsoft Word"
            set theDoc to open inputPath
            save as theDoc file format format PDF file name outputPath
            close theDoc saving no
        end tell
        '''
        subprocess.run(
            ['osascript', '-e', script],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=180,
        )
        if os.path.exists(output_path):
            return output_path

    raise RuntimeError('未找到可用的 Word 转 PDF 工具。请安装 LibreOffice，或确认本机 Microsoft Word 可被 AppleScript 调用。')

def _render_pdf_to_long_image(pdf_path, output_path, image_format='png', target_width=1440, zoom=2.0, quality=92):
    import fitz  # type: ignore
    from PIL import Image

    image_format = image_format.lower()
    if image_format not in {'png', 'jpg', 'jpeg'}:
        raise ValueError('输出格式只支持 PNG 或 JPG')

    target_width = int(target_width or 0)
    zoom = float(zoom or 2.0)
    quality = max(60, min(100, int(quality or 92)))

    doc = fitz.open(pdf_path)
    try:
        if doc.page_count == 0:
            raise ValueError('PDF 没有可渲染页面')
        if doc.page_count > LONG_IMAGE_MAX_PAGES:
            raise ValueError(f'页数过多，最多支持 {LONG_IMAGE_MAX_PAGES} 页')

        pages = []
        total_pixels = 0
        matrix = fitz.Matrix(zoom, zoom)

        for page in doc:
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            img = Image.frombytes('RGB', [pix.width, pix.height], pix.samples)
            if target_width > 0 and img.width != target_width:
                new_height = max(1, round(img.height * (target_width / img.width)))
                img = img.resize((target_width, new_height), Image.Resampling.LANCZOS)
            pages.append(img)
            total_pixels += img.width * img.height

        if total_pixels > LONG_IMAGE_MAX_PIXELS:
            raise ValueError('长图尺寸过大，请降低输出宽度或拆分文档后再试')

        output_width = max(page.width for page in pages)
        output_height = sum(page.height for page in pages)
        long_image = Image.new('RGB', (output_width, output_height), 'white')
        y = 0
        for page in pages:
            x = (output_width - page.width) // 2
            long_image.paste(page, (x, y))
            y += page.height

        save_kwargs = {}
        if image_format in {'jpg', 'jpeg'}:
            save_kwargs.update({'quality': quality, 'optimize': True, 'progressive': True})
        long_image.save(output_path, **save_kwargs)

        return {
            'page_count': doc.page_count,
            'width': output_width,
            'height': output_height,
            'file_size': os.path.getsize(output_path),
        }
    finally:
        doc.close()

def _pad_image_min_height(image_path, min_height):
    from PIL import Image

    with Image.open(image_path) as img:
        img = img.convert('RGB')
        if img.height >= min_height:
            return {'width': img.width, 'height': img.height}

        padded = Image.new('RGB', (img.width, min_height), 'white')
        padded.paste(img, (0, 0))
        padded.save(image_path)
        return {'width': padded.width, 'height': padded.height}

def _download_pdf_url(pdf_url, output_path):
    parsed = urllib.request.urlparse(pdf_url)
    if parsed.scheme not in {'http', 'https'}:
        raise ValueError('PDF 链接只支持 http 或 https')

    request_obj = urllib.request.Request(
        pdf_url,
        headers={'User-Agent': 'Shioa PDF Scroll Video/1.0'}
    )
    total = 0
    with urllib.request.urlopen(request_obj, timeout=30) as response:
        with open(output_path, 'wb') as out:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
                if total > PDF_VIDEO_MAX_FILE_SIZE:
                    raise ValueError(f'PDF 文件过大。最大支持 {get_file_size_str(PDF_VIDEO_MAX_FILE_SIZE)}')
                out.write(chunk)

def _generate_pdf_scroll_video(long_image_path, output_path, width, height, duration, start_hold, end_hold, fps, crf):
    scroll_duration = max(0.1, duration - start_hold - end_hold)
    scroll_end_time = start_hold + scroll_duration
    y_expr = (
        f"if(lt(t\\,{start_hold:.3f})\\,0\\,"
        f"if(gt(t\\,{scroll_end_time:.3f})\\,ih-oh\\,"
        f"(ih-oh)*(t-{start_hold:.3f})/{scroll_duration:.3f}))"
    )
    video_filter = (
        f"crop={width}:{height}:0:'{y_expr}',"
        f"fps={fps},format=yuv420p"
    )
    cmd = [
        'ffmpeg',
        '-y',
        '-loop',
        '1',
        '-i',
        long_image_path,
        '-t',
        f'{duration:.3f}',
        '-vf',
        video_filter,
        '-c:v',
        'libx264',
        '-preset',
        'medium',
        '-crf',
        str(crf),
        '-movflags',
        '+faststart',
        output_path,
    ]
    subprocess.run(
        cmd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=max(120, int(duration * 8)),
    )

def _find_chinese_font():
    candidates = [
        '/System/Library/Fonts/PingFang.ttc',
        '/System/Library/Fonts/STHeiti Medium.ttc',
        '/System/Library/Fonts/Hiragino Sans GB.ttc',
        '/System/Library/Fonts/Supplemental/Arial Unicode.ttf',
        '/System/Library/Fonts/Supplemental/Songti.ttc',
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return candidate
    return None

def _probe_media_duration(media_path):
    cmd = [
        'ffprobe',
        '-v',
        'error',
        '-show_entries',
        'format=duration',
        '-of',
        'default=noprint_wrappers=1:nokey=1',
        media_path,
    ]
    result = subprocess.run(
        cmd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30,
    )
    return max(0.1, float(result.stdout.strip()))

def _is_supported_podcast_audio_path(path_obj):
    if path_obj.suffix.lower() in PODCAST_AUDIO_EXTENSIONS:
        return True
    try:
        _probe_media_duration(str(path_obj))
        return True
    except Exception:
        return False

def _parse_timecode(value):
    value = str(value or '').strip()
    if not value:
        return None
    if re.fullmatch(r'\d+(\.\d+)?', value):
        seconds = float(value)
    else:
        parts = value.split(':')
        if len(parts) not in {2, 3}:
            raise ValueError('时间格式请填写秒数、MM:SS 或 HH:MM:SS')
        try:
            numeric_parts = [float(part) for part in parts]
        except ValueError as exc:
            raise ValueError('时间格式请填写秒数、MM:SS 或 HH:MM:SS') from exc
        if len(parts) == 2:
            minutes, seconds_part = numeric_parts
            seconds = minutes * 60 + seconds_part
        else:
            hours, minutes, seconds_part = numeric_parts
            seconds = hours * 3600 + minutes * 60 + seconds_part
    if seconds < 0:
        raise ValueError('时间戳不能小于 0')
    return seconds

def _parse_audio_speed(value):
    try:
        speed = float(value or 1)
    except ValueError as exc:
        raise ValueError('倍速必须是数字') from exc
    if speed < 0.5 or speed > 2.0:
        raise ValueError('音频倍速支持 0.5 到 2.0')
    return speed

def _atempo_filter(speed):
    speed = float(speed or 1)
    if abs(speed - 1) < 0.001:
        return None
    return f'atempo={speed:.6f}'.rstrip('0').rstrip('.')

def _prepare_podcast_audio(input_path, output_path, start_seconds=None, end_seconds=None, speed=1.0):
    original_duration = _probe_media_duration(input_path)
    start_seconds = 0.0 if start_seconds is None else float(start_seconds)
    end_seconds = original_duration if end_seconds is None else min(float(end_seconds), original_duration)
    speed = _parse_audio_speed(speed)

    if start_seconds >= original_duration:
        raise ValueError('开始时间不能晚于音频总时长')
    if end_seconds <= start_seconds:
        raise ValueError('截止时间必须晚于开始时间')

    trim_duration = end_seconds - start_seconds
    cmd = ['ffmpeg', '-y']
    if start_seconds > 0:
        cmd.extend(['-ss', f'{start_seconds:.3f}'])
    cmd.extend(['-i', input_path])
    cmd.extend(['-t', f'{trim_duration:.3f}'])
    audio_filter = _atempo_filter(speed)
    if audio_filter:
        cmd.extend(['-filter:a', audio_filter])
    cmd.extend(['-vn', '-acodec', 'pcm_s16le', '-ar', '44100', '-ac', '2', output_path])
    subprocess.run(
        cmd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=max(120, int(trim_duration * 4)),
    )
    processed_duration = _probe_media_duration(output_path)
    return {
        'source_duration': original_duration,
        'start_seconds': start_seconds,
        'end_seconds': end_seconds,
        'speed': speed,
        'processed_duration': processed_duration,
    }

def _retime_segments_for_audio(segments, start_seconds=0.0, end_seconds=None, speed=1.0):
    if not segments:
        return []
    start_seconds = float(start_seconds or 0)
    end_seconds = None if end_seconds is None else float(end_seconds)
    speed = float(speed or 1)
    retimed = []
    for segment in segments:
        source_start = float(segment.get('start', 0))
        source_end = float(segment.get('end', source_start + 1))
        if end_seconds is not None and source_start >= end_seconds:
            continue
        if source_end <= start_seconds:
            continue
        clipped_start = max(source_start, start_seconds)
        clipped_end = min(source_end, end_seconds) if end_seconds is not None else source_end
        if clipped_end <= clipped_start:
            continue
        retimed.append({
            'start': (clipped_start - start_seconds) / speed,
            'end': (clipped_end - start_seconds) / speed,
            'text': segment.get('text', ''),
        })
    return retimed

def _fit_text_to_width(draw, text, font_path, start_size, max_width, min_size=24):
    from PIL import ImageFont

    size = start_size
    while size > min_size:
        font = ImageFont.truetype(font_path, size=size)
        if draw.textbbox((0, 0), text, font=font)[2] <= max_width:
            return font
        size -= 2
    return ImageFont.truetype(font_path, size=min_size)

def _wrap_text(draw, text, font, max_width, max_lines=3, max_chars_per_line=15, add_ellipsis=True):
    if not text:
        return []

    lines = []
    current = ''
    for char in text:
        test_line = f'{current}{char}'
        width = draw.textbbox((0, 0), test_line, font=font)[2]
        if (width <= max_width and len(test_line) <= max_chars_per_line) or not current:
            current = test_line
        else:
            lines.append(current)
            current = char
            if len(lines) >= max_lines:
                break

    if current and len(lines) < max_lines:
        lines.append(current)

    consumed = ''.join(lines)
    if add_ellipsis and len(consumed) < len(text) and lines:
        while draw.textbbox((0, 0), f'{lines[-1]}...', font=font)[2] > max_width and lines[-1]:
            lines[-1] = lines[-1][:-1]
        lines[-1] = f'{lines[-1]}...'

    return lines

def _fit_wrapped_text(draw, text, font_path, start_size, max_width, max_lines=3, max_chars_per_line=18, min_size=28):
    from PIL import ImageFont

    for size in range(start_size, min_size - 1, -2):
        font = ImageFont.truetype(font_path, size=size)
        lines = _wrap_text(
            draw,
            text,
            font,
            max_width,
            max_lines=max_lines,
            max_chars_per_line=max_chars_per_line,
            add_ellipsis=False,
        )
        if ''.join(lines) == text:
            return font, lines

    font = ImageFont.truetype(font_path, size=min_size)
    lines = _wrap_text(
        draw,
        text,
        font,
        max_width,
        max_lines=max_lines,
        max_chars_per_line=max_chars_per_line,
        add_ellipsis=True,
    )
    return font, lines

def _render_podcast_text_overlay(output_path, width, height, title, subtitle, brand_text, series_text, accent_color, caption_text=''):
    from PIL import Image, ImageDraw, ImageFont

    font_path = _find_chinese_font()
    if not font_path:
        raise RuntimeError('未找到可用中文字体，无法渲染标题')

    overlay = Image.new('RGBA', (width, height), (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)

    title = (title or '播客标题').strip()
    subtitle = (subtitle or '').strip()
    series_text = (series_text or '').strip()

    x = int(width * 0.08)
    max_text_width = int(width * 0.445)
    series_font = ImageFont.truetype(font_path, size=int(width * 0.018))
    subtitle_font = ImageFont.truetype(font_path, size=int(width * 0.0135))

    title_font, lines = _fit_wrapped_text(
        draw,
        title,
        font_path,
        start_size=int(width * 0.031),
        max_width=max_text_width,
        max_lines=3,
        max_chars_per_line=20,
        min_size=int(width * 0.024),
    )
    title_y = int(height * 0.098)
    line_height = int(title_font.size * 1.16)
    for index, line in enumerate(lines):
        draw.text((x, title_y + index * line_height), line, font=title_font, fill=(42, 38, 34, 235))

    next_y = title_y + len(lines) * line_height + int(height * 0.020)
    if series_text:
        series_lines = _wrap_text(draw, series_text, series_font, max_text_width, max_lines=1, max_chars_per_line=24)
        for index, line in enumerate(series_lines):
            draw.text((x, next_y + index * int(series_font.size * 1.18)), line, font=series_font, fill=(55, 49, 43, 220))
        next_y += len(series_lines) * int(series_font.size * 1.18) + int(height * 0.014)

    if subtitle:
        draw.text(
            (x, next_y),
            subtitle[:80],
            font=subtitle_font,
            fill=(92, 82, 72, 190),
        )

    overlay.save(output_path)

def _render_podcast_record_disc(output_path, size, accent_color, brand_text='胶囊播客'):
    from PIL import Image, ImageDraw, ImageFont

    accent_color = (accent_color or '#b9895e').strip()
    brand_text = (brand_text or '胶囊播客').strip()
    disc = Image.new('RGBA', (size, size), (255, 255, 255, 0))
    draw = ImageDraw.Draw(disc)
    center = size // 2
    outer_radius = int(size * 0.345)
    label_radius = int(size * 0.118)

    draw.ellipse(
        (
            center - outer_radius,
            center - outer_radius,
            center + outer_radius,
            center + outer_radius,
        ),
        fill=(15, 15, 14, 248),
    )
    draw.ellipse(
        (
            center - int(size * 0.305),
            center - int(size * 0.305),
            center + int(size * 0.305),
            center + int(size * 0.305),
        ),
        fill=(22, 22, 21, 190),
    )
    for ratio, alpha in ((0.285, 36), (0.225, 26)):
        radius = int(size * ratio)
        draw.ellipse(
            (
                center - radius,
                center - radius,
                center + radius,
                center + radius,
            ),
            outline=(74, 70, 63, alpha),
            width=1,
        )
    highlight_radius = int(size * 0.275)
    draw.arc(
        (
            center - highlight_radius,
            center - highlight_radius,
            center + highlight_radius,
            center + highlight_radius,
        ),
        start=205,
        end=322,
        fill=(255, 255, 255, 5),
        width=max(1, size // 150),
    )
    draw.ellipse(
        (
            center - label_radius,
            center - label_radius,
            center + label_radius,
            center + label_radius,
        ),
        fill=(203, 137, 70, 248),
        outline=(255, 248, 236, 160),
        width=max(2, size // 115),
    )
    font_path = _find_chinese_font()
    if font_path:
        brand_font = _fit_text_to_width(draw, brand_text, font_path, int(size * 0.064), int(size * 0.21), min_size=max(11, size // 34))
        bbox = draw.textbbox((0, 0), brand_text, font=brand_font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
        text_x = center - text_w // 2
        text_y = center - text_h // 2
        draw.text(
            (text_x, text_y),
            brand_text,
            font=brand_font,
            fill=(255, 248, 236, 235),
        )
    disc.save(output_path)

def _srt_time_to_seconds(value):
    match = re.match(r'(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})', value.strip())
    if not match:
        return 0.0
    hours, minutes, seconds, millis = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(millis.ljust(3, '0')) / 1000

def _parse_srt_content(content):
    content = content.replace('\r\n', '\n').replace('\r', '\n').strip()
    if not content:
        return []

    blocks = re.split(r'\n{2,}', content)
    segments = []
    for block in blocks:
        lines = [line.strip() for line in block.split('\n') if line.strip()]
        if not lines:
            continue
        time_index = 0
        if '-->' not in lines[0] and len(lines) > 1:
            time_index = 1
        if time_index >= len(lines) or '-->' not in lines[time_index]:
            continue
        start_raw, end_raw = [part.strip().split()[0] for part in lines[time_index].split('-->', 1)]
        text = ''.join(lines[time_index + 1:]).strip()
        if not text:
            continue
        start = _srt_time_to_seconds(start_raw)
        end = _srt_time_to_seconds(end_raw)
        if end <= start:
            end = start + 1.5
        segments.append({'start': start, 'end': end, 'text': text})
    return segments

def _segments_from_asr(audio_path, model_size='medium'):
    from services.asr_cache_service import transcribe_with_cache
    return transcribe_with_cache(audio_path, model_size=model_size)

def _segments_from_cached_asr(source_path, model_size='medium', start_seconds=None, end_seconds=None, speed=1.0, prepared_audio_path=None):
    from services.asr_cache_service import transcribe_with_cache
    return transcribe_with_cache(
        source_path,
        model_size=model_size,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        speed=speed,
        prepared_audio_path=prepared_audio_path,
    )

def _ass_time(seconds):
    seconds = max(0, float(seconds or 0))
    centiseconds = int(round(seconds * 100))
    cs = centiseconds % 100
    total_seconds = centiseconds // 100
    s = total_seconds % 60
    total_minutes = total_seconds // 60
    m = total_minutes % 60
    h = total_minutes // 60
    return f'{h}:{m:02d}:{s:02d}.{cs:02d}'

def _ass_escape(text):
    return str(text or '').replace('\\', '\\\\').replace('{', '').replace('}', '').replace('\n', ' ')

def _wrap_ass_text(text, max_chars=16):
    text = str(text or '').strip()
    if len(text) <= max_chars:
        return _ass_escape(text)
    chunks = [text[index:index + max_chars] for index in range(0, len(text), max_chars)]
    return r'\N'.join(_ass_escape(chunk) for chunk in chunks[:2])

def _write_podcast_lyrics_ass(segments, output_path, width, height):
    font_name = 'Arial Unicode MS'
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Past2,{font_name},28,&HBB75685C,&H000000FF,&H22FFFFFF,&H00000000,0,0,0,0,100,100,0,0,1,0,0,4,154,1010,0,1
Style: Past1,{font_name},31,&H8875685C,&H000000FF,&H33FFFFFF,&H00000000,0,0,0,0,100,100,0,0,1,0,0,4,154,1010,0,1
Style: Current,{font_name},42,&H0022262A,&H000000FF,&HAAFFFFFF,&H00000000,1,0,0,0,100,100,0,0,1,1,0,4,154,1010,0,1
Style: Future1,{font_name},31,&H8875685C,&H000000FF,&H33FFFFFF,&H00000000,0,0,0,0,100,100,0,0,1,0,0,4,154,1010,0,1
Style: Future2,{font_name},28,&HBB75685C,&H000000FF,&H22FFFFFF,&H00000000,0,0,0,0,100,100,0,0,1,0,0,4,154,1010,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events = []
    for index, segment in enumerate(segments):
        start = float(segment.get('start', 0))
        end = float(segment.get('end', start + 2))
        if end <= start:
            end = start + 2
        lyric_rows = [
            ('Past2', segments[index - 2]['text'] if index > 1 else '', int(height * 0.405), 18),
            ('Past1', segments[index - 1]['text'] if index > 0 else '', int(height * 0.465), 18),
            ('Current', segment.get('text', ''), int(height * 0.545), 16),
            ('Future1', segments[index + 1]['text'] if index + 1 < len(segments) else '', int(height * 0.630), 18),
            ('Future2', segments[index + 2]['text'] if index + 2 < len(segments) else '', int(height * 0.690), 18),
        ]
        visible_start = max(0, start - 0.18)
        visible_end = end + 0.24
        duration_ms = max(900, int((visible_end - visible_start) * 1000))
        drift = max(16, int(height * 0.022))
        for style, text, y, max_chars in lyric_rows:
            if not text:
                continue
            fade_in = 160 if style == 'Current' else 220
            fade_out = 180 if style == 'Current' else 240
            layer = 1 if style == 'Current' else 0
            events.append(
                f"Dialogue: {layer},{_ass_time(visible_start)},{_ass_time(visible_end)},{style},,0,0,500,,"
                f"{{\\fad({fade_in},{fade_out})\\move(154,{y + drift},154,{y - drift},0,{duration_ms})}}"
                f"{_wrap_ass_text(text, max_chars)}"
            )

    Path(output_path).write_text(header + '\n'.join(events) + '\n', encoding='utf-8')

def _ffmpeg_filter_escape(path):
    return str(path).replace('\\', '\\\\').replace(':', '\\:').replace("'", "\\'")

def _podcast_visualizer_layout(width, height):
    return {
        'disc_size': int(width * 0.245),
        'disc_x': int(width * 0.637),
        'disc_y': int(height * 0.238),
        'progress_width': int(width * 0.80),
        'progress_x': int(width * 0.10),
        'progress_y': int(height * 0.825),
        'waves_width': int(width * 0.72),
        'waves_height': int(height * 0.045),
        'waves_x': int(width * 0.14),
        'waves_y': int(height * 0.835),
    }

def _build_podcast_visualizer_filter(width, height, duration, lyrics_ass_path=None, output_format='yuv420p'):
    layout = _podcast_visualizer_layout(width, height)
    duration = max(0.1, float(duration or 0.1))
    base_filter = (
        f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},format=rgba[bg];"
        f"[2:v]scale={width}:{height},format=rgba[text];"
        f"[3:v]format=rgba,rotate=2*PI*t/18:ow=iw:oh=ih:"
        f"c=none,scale={layout['disc_size']}:{layout['disc_size']}:force_original_aspect_ratio=decrease[disc];"
        f"[1:a]volume=9dB,showwaves=s={layout['waves_width']}x{layout['waves_height']}:mode=p2p:colors=white@0.72:scale=sqrt,"
        f"format=rgba[waves];"
        f"[bg][text]overlay=0:0[base];"
        f"[base][disc]overlay={layout['disc_x']}:{layout['disc_y']}:format=auto[spinning];"
        f"[spinning][waves]overlay={layout['waves_x']}:{layout['waves_y']}:format=auto[withwaves];"
    )
    if lyrics_ass_path:
        fonts_dir = '/System/Library/Fonts/Supplemental'
        return (
            base_filter
            + f"[withwaves]ass='{_ffmpeg_filter_escape(lyrics_ass_path)}':fontsdir='{_ffmpeg_filter_escape(fonts_dir)}',format={output_format}[v]"
        )
    return base_filter + f"[withwaves]format={output_format}[v]"

def _podcast_preview_cache_path(payload):
    template_mtime = os.path.getmtime(PODCAST_TEMPLATE_PATH) if os.path.exists(PODCAST_TEMPLATE_PATH) else 0
    cache_payload = {
        'renderer_version': PODCAST_VISUALIZER_RENDERER_VERSION,
        'template_mtime': template_mtime,
        **payload,
    }
    digest = hashlib.sha256(
        json.dumps(cache_payload, ensure_ascii=False, sort_keys=True).encode('utf-8')
    ).hexdigest()
    return PODCAST_PREVIEW_CACHE_DIR / f'{digest}.png'

def _podcast_visualizer_video_bitrate(crf):
    crf = int(crf or 20)
    if crf <= 18:
        return '8000k'
    if crf <= 20:
        return '6000k'
    if crf <= 24:
        return '3500k'
    return '2500k'

def _podcast_visualizer_encoder_args(crf):
    if platform.system() == 'Darwin':
        return [
            '-c:v',
            'h264_videotoolbox',
            '-allow_sw',
            '1',
            '-b:v',
            _podcast_visualizer_video_bitrate(crf),
            '-tag:v',
            'avc1',
        ], True
    return [
        '-c:v',
        'libx264',
        '-preset',
        'veryfast',
        '-crf',
        str(crf),
    ], False

def _podcast_visualizer_fallback_encoder_args(crf):
    return [
        '-c:v',
        'libx264',
        '-preset',
        'veryfast',
        '-crf',
        str(crf),
    ]

def _generate_podcast_visualizer_video(background_path, overlay_path, audio_path, output_path, width, height, duration, fps, crf, brand_text='胶囊播客', lyrics_ass_path=None):
    temp_dir = os.path.dirname(output_path)
    layout = _podcast_visualizer_layout(width, height)
    disc_size = layout['disc_size']
    disc_path = os.path.join(temp_dir, 'record_disc.png')
    _render_podcast_record_disc(disc_path, disc_size, '#b9895e', brand_text=brand_text)
    filter_complex = _build_podcast_visualizer_filter(
        width=width,
        height=height,
        duration=duration,
        lyrics_ass_path=lyrics_ass_path,
        output_format='yuv420p',
    )
    encoder_args, uses_hardware_encoder = _podcast_visualizer_encoder_args(crf)
    cmd = [
        'ffmpeg',
        '-y',
        '-loop',
        '1',
        '-i',
        background_path,
        '-i',
        audio_path,
        '-loop',
        '1',
        '-i',
        overlay_path,
        '-loop',
        '1',
        '-i',
        disc_path,
        '-filter_complex',
        filter_complex,
        '-map',
        '[v]',
        '-map',
        '1:a',
        '-t',
        f'{duration:.3f}',
        '-r',
        str(fps),
        *encoder_args,
        '-pix_fmt',
        'yuv420p',
        '-c:a',
        'aac',
        '-b:a',
        '192k',
        '-movflags',
        '+faststart',
        output_path,
    ]
    try:
        logger.info(
            "🎬 [PODCAST_RENDER] encoder=%s duration=%.2fs fps=%s crf=%s",
            encoder_args[1],
            duration,
            fps,
            crf,
        )
        subprocess.run(
            cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(180, int(duration * 10)),
        )
    except subprocess.CalledProcessError as exc:
        if not uses_hardware_encoder:
            raise
        logger.warning("⚠️ [PODCAST_RENDER] h264_videotoolbox 不可用，回退到 libx264: %s", exc.stderr[-500:] if exc.stderr else exc)
        fallback_args = _podcast_visualizer_fallback_encoder_args(crf)
        fallback_cmd = [
            *cmd[:cmd.index('-c:v')],
            *fallback_args,
            '-pix_fmt',
            'yuv420p',
            *cmd[cmd.index('-c:a'):],
        ]
        subprocess.run(
            fallback_cmd,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(180, int(duration * 10)),
        )

def _generate_podcast_visualizer_preview_frame(background_path, overlay_path, output_path, width, height, brand_text='胶囊播客', lyrics_ass_path=None):
    temp_dir = os.path.dirname(output_path)
    duration = 6.0
    frame_time = 2.4
    layout = _podcast_visualizer_layout(width, height)
    disc_size = layout['disc_size']
    disc_path = os.path.join(temp_dir, 'record_disc.png')
    _render_podcast_record_disc(disc_path, disc_size, '#b9895e', brand_text=brand_text)
    filter_complex = _build_podcast_visualizer_filter(
        width=width,
        height=height,
        duration=duration,
        lyrics_ass_path=lyrics_ass_path,
        output_format='rgba',
    )

    cmd = [
        'ffmpeg',
        '-y',
        '-loop',
        '1',
        '-i',
        background_path,
        '-f',
        'lavfi',
        '-i',
        f'sine=frequency=420:sample_rate=44100:duration={duration:.3f}',
        '-loop',
        '1',
        '-i',
        overlay_path,
        '-loop',
        '1',
        '-i',
        disc_path,
        '-filter_complex',
        filter_complex,
        '-map',
        '[v]',
        '-ss',
        f'{frame_time:.3f}',
        '-frames:v',
        '1',
        output_path,
    ]
    subprocess.run(
        cmd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
    )

@bp.route('/convert', methods=['POST'])
def convert_document():
    """
    文档转换接口
    """
    logger.info("📨 收到文档转换请求")
    try:
        # 检查是否有文件上传
        if 'file' not in request.files:
            logger.warning("⚠️  请求中没有文件")
            return jsonify({
                'success': False,
                'message': '没有选择文件'
            }), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({
                'success': False,
                'message': '没有选择文件'
            }), 400
        
        # 获取转换参数
        source_format = request.form.get('source_format', '').lower()
        target_format = request.form.get('target_format', '').lower()
        
        logger.info(f"📋 转换参数: {source_format} -> {target_format}")
        logger.info(f"📁 文件信息: {file.filename}, 大小: {file.tell() if hasattr(file, 'tell') else '未知'}")
        
        if not source_format or not target_format:
            return jsonify({
                'success': False,
                'message': '缺少源格式或目标格式参数'
            }), 400
        
        # 验证支持的格式
        supported_formats = ['markdown', 'docx']
        if source_format not in supported_formats or target_format not in supported_formats:
            return jsonify({
                'success': False,
                'message': f'不支持的格式。支持的格式：{", ".join(supported_formats)}'
            }), 400
        
        if source_format == target_format:
            return jsonify({
                'success': False,
                'message': '源格式和目标格式不能相同'
            }), 400
        
        # 检查文件扩展名
        if not allowed_file(file.filename, source_format):
            return jsonify({
                'success': False,
                'message': f'不支持的文件类型。{source_format}格式支持的扩展名：{", ".join(ALLOWED_EXTENSIONS[source_format])}'
            }), 400
        
        # 检查文件大小
        file.seek(0, 2)  # 移到文件末尾
        file_size = file.tell()
        file.seek(0)     # 回到文件开头
        
        if file_size > MAX_FILE_SIZE:
            return jsonify({
                'success': False,
                'message': f'文件过大。最大支持 {get_file_size_str(MAX_FILE_SIZE)}'
            }), 400
        
        # 生成唯一的文件名
        unique_id = str(uuid.uuid4())
        secure_name = secure_filename(file.filename)
        base_name = os.path.splitext(secure_name)[0]
        
        # 创建临时目录
        temp_dir = tempfile.mkdtemp()
        
        try:
            # 保存上传的文件
            source_ext = ALLOWED_EXTENSIONS[source_format][0]
            if source_format == 'docx':
                source_ext = '.docx'
            
            input_path = os.path.join(temp_dir, f"{unique_id}_input{source_ext}")
            file.save(input_path)
            
            # 设置输出文件路径
            target_ext = ALLOWED_EXTENSIONS[target_format][0]
            if target_format == 'docx':
                target_ext = '.docx'
            
            output_filename = f"{base_name}_converted{target_ext}"
            output_path = os.path.join(temp_dir, output_filename)
            
            logger.info(f"开始转换文档: {source_format} -> {target_format}")
            logger.info(f"输入文件: {input_path}")
            logger.info(f"输出文件: {output_path}")
            
            # 使用pypandoc进行转换
            import pypandoc
            if source_format == 'markdown' and target_format == 'docx':
                # Markdown 转 Word
                pypandoc.convert_file(
                    input_path,
                    'docx',
                    outputfile=output_path,
                    extra_args=['--standalone']
                )
            elif source_format == 'docx' and target_format == 'markdown':
                # Word 转 Markdown
                pypandoc.convert_file(
                    input_path,
                    'markdown',
                    outputfile=output_path,
                    extra_args=['--standalone']
                )
            
            # 检查输出文件是否生成成功
            if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
                return jsonify({
                    'success': False,
                    'message': '文档转换失败，请检查文件格式是否正确'
                }), 500
            
            logger.info(f"文档转换成功: {output_filename}")
            
            # 返回转换后的文件
            return send_file(
                output_path,
                as_attachment=True,
                download_name=output_filename,
                mimetype='application/octet-stream'
            )
        
        except Exception as e:
            logger.error(f"💥 文档转换过程中出错: {str(e)}")
            logger.error(f"📝 异常详情: {type(e).__name__}: {str(e)}")
            import traceback
            logger.error(f"📊 异常堆栈:\n{traceback.format_exc()}")
            return jsonify({
                'success': False,
                'message': f'文档转换失败: {str(e)}'
            }), 500
        
        finally:
            # 清理临时文件
            try:
                if os.path.exists(temp_dir):
                    import shutil
                    shutil.rmtree(temp_dir)
            except Exception as e:
                logger.warning(f"清理临时文件失败: {str(e)}")
    
    except Exception as e:
        logger.error(f"🔥 处理文档转换请求时出现未捕获异常: {str(e)}")
        import traceback
        logger.error(f"📊 顶级异常堆栈:\n{traceback.format_exc()}")
        return jsonify({
            'success': False,
            'message': f'服务器内部错误: {str(e)}'
        }), 500

@bp.route('/supported-formats', methods=['GET'])
def get_supported_formats():
    """
    获取支持的文档格式
    """
    return jsonify({
        'success': True,
        'formats': {
            'markdown': {
                'name': 'Markdown',
                'extensions': ALLOWED_EXTENSIONS['markdown'],
                'description': 'Markdown文档格式'
            },
            'docx': {
                'name': 'Microsoft Word',
                'extensions': ALLOWED_EXTENSIONS['docx'],
                'description': 'Microsoft Word文档格式'
            }
        },
        'max_file_size': MAX_FILE_SIZE,
        'max_file_size_str': get_file_size_str(MAX_FILE_SIZE)
    })

@bp.route('/health', methods=['GET'])
def health_check():
    """
    健康检查接口
    """
    try:
        # 检查pandoc是否可用
        import pypandoc
        version = pypandoc.get_pandoc_version()
        return jsonify({
            'success': True,
            'message': '文档转换服务正常',
            'pandoc_version': version,
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'message': f'Pandoc不可用: {str(e)}'
        }), 500

@bp.route('/word-long-image', methods=['POST'])
def convert_word_to_long_image():
    """
    Word 文档转长图。
    路线：Word -> PDF -> 按页图片 -> 纵向拼接长图。
    """
    logger.info("📨 收到 Word 转长图请求")
    temp_dir = tempfile.mkdtemp(prefix='word_long_image_')
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'message': '没有选择文件'}), 400

        file = request.files['file']
        if not file or file.filename == '':
            return jsonify({'success': False, 'message': '没有选择文件'}), 400

        ext = os.path.splitext(file.filename.lower())[1]
        if ext not in {'.docx', '.doc'}:
            return jsonify({'success': False, 'message': '只支持 .docx 和 .doc 文件'}), 400

        file.seek(0, 2)
        file_size = file.tell()
        file.seek(0)
        if file_size > LONG_IMAGE_MAX_FILE_SIZE:
            return jsonify({
                'success': False,
                'message': f'文件过大。最大支持 {get_file_size_str(LONG_IMAGE_MAX_FILE_SIZE)}'
            }), 400

        image_format = request.form.get('format', 'png').lower()
        if image_format == 'jpeg':
            image_format = 'jpg'
        if image_format not in {'png', 'jpg'}:
            return jsonify({'success': False, 'message': '输出格式只支持 PNG 或 JPG'}), 400

        try:
            target_width = int(request.form.get('width', '1440'))
        except ValueError:
            target_width = 1440
        target_width = max(720, min(2400, target_width))

        try:
            quality = int(request.form.get('quality', '92'))
        except ValueError:
            quality = 92

        original_base = _safe_base_name(file.filename)
        safe_name = secure_filename(file.filename) or f'document{ext}'
        if not os.path.splitext(safe_name)[1]:
            safe_name = f'{safe_name}{ext}'
        input_path = os.path.join(temp_dir, safe_name)
        file.save(input_path)

        logger.info(f"🔄 Word 转 PDF: {file.filename}")
        pdf_path = _convert_word_to_pdf(input_path, temp_dir)

        output_ext = 'jpg' if image_format == 'jpg' else 'png'
        output_filename = f"{original_base}.long.{target_width}px.{output_ext}"
        output_path = os.path.join(temp_dir, f"output.{output_ext}")

        logger.info(f"🖼️ PDF 转长图: {pdf_path}")
        meta = _render_pdf_to_long_image(
            pdf_path,
            output_path,
            image_format=image_format,
            target_width=target_width,
            quality=quality,
        )
        logger.info(f"✅ Word 转长图成功: {output_filename}, {meta}")

        mimetype = 'image/jpeg' if image_format == 'jpg' else 'image/png'
        response = send_file(
            output_path,
            as_attachment=True,
            download_name=output_filename,
            mimetype=mimetype,
        )
        response.headers['X-Page-Count'] = str(meta['page_count'])
        response.headers['X-Image-Width'] = str(meta['width'])
        response.headers['X-Image-Height'] = str(meta['height'])
        response.headers['X-File-Size'] = str(meta['file_size'])
        response.call_on_close(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        return response

    except subprocess.TimeoutExpired:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return jsonify({'success': False, 'message': '转换超时，请确认 Word 文档能正常打开，或减少页数后重试'}), 504
    except subprocess.CalledProcessError as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        logger.error(f"Word 转 PDF 工具执行失败: {detail}")
        return jsonify({'success': False, 'message': f'Word 转 PDF 失败: {detail or exc}'}), 500
    except Exception as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        logger.error(f"Word 转长图失败: {exc}", exc_info=True)
        return jsonify({'success': False, 'message': f'Word 转长图失败: {exc}'}), 500

@bp.route('/pdf-scroll-video', methods=['POST'])
def convert_pdf_to_scroll_video():
    """
    PDF 转匀速滚动 MP4。
    路线：PDF -> 长图 -> FFmpeg crop 动画 -> H.264 MP4。
    """
    logger.info("📨 收到 PDF 滚动视频请求")
    temp_dir = tempfile.mkdtemp(prefix='pdf_scroll_video_')
    try:
        file = request.files.get('file')
        pdf_url = request.form.get('pdf_url', '').strip()

        if not file and not pdf_url:
            return jsonify({'success': False, 'message': '请上传 PDF 文件或填写 PDF 链接'}), 400

        try:
            width = int(request.form.get('width', '1080'))
            height = int(request.form.get('height', '1920'))
            duration = float(request.form.get('duration', '30'))
            start_hold = float(request.form.get('start_hold', '1'))
            end_hold = float(request.form.get('end_hold', '1'))
            fps = int(request.form.get('fps', '30'))
            crf = int(request.form.get('crf', '20'))
        except ValueError:
            return jsonify({'success': False, 'message': '视频参数格式不正确'}), 400

        width = max(360, min(3840, width))
        height = max(360, min(3840, height))
        duration = max(3, min(600, duration))
        start_hold = max(0, min(30, start_hold))
        end_hold = max(0, min(30, end_hold))
        fps = max(12, min(60, fps))
        crf = max(16, min(32, crf))

        if start_hold + end_hold >= duration:
            start_hold = 0
            end_hold = 0

        if shutil.which('ffmpeg') is None:
            return jsonify({'success': False, 'message': '当前环境未找到 FFmpeg，无法生成 MP4'}), 500

        if file:
            if file.filename == '':
                return jsonify({'success': False, 'message': '没有选择文件'}), 400
            ext = os.path.splitext(file.filename.lower())[1]
            if ext != '.pdf':
                return jsonify({'success': False, 'message': '只支持 .pdf 文件'}), 400
            file.seek(0, 2)
            file_size = file.tell()
            file.seek(0)
            if file_size > PDF_VIDEO_MAX_FILE_SIZE:
                return jsonify({
                    'success': False,
                    'message': f'PDF 文件过大。最大支持 {get_file_size_str(PDF_VIDEO_MAX_FILE_SIZE)}'
                }), 400
            original_base = _safe_base_name(file.filename)
            input_path = os.path.join(temp_dir, 'input.pdf')
            file.save(input_path)
        else:
            original_base = _safe_base_name(Path(urllib.request.urlparse(pdf_url).path).name or 'pdf-scroll')
            input_path = os.path.join(temp_dir, 'input.pdf')
            _download_pdf_url(pdf_url, input_path)

        long_image_path = os.path.join(temp_dir, 'long.png')
        output_filename = f"{original_base}.scroll.{width}x{height}.{int(duration)}s.mp4"
        output_path = os.path.join(temp_dir, 'output.mp4')

        logger.info(f"🖼️ PDF 渲染长图: {input_path}")
        meta = _render_pdf_to_long_image(
            input_path,
            long_image_path,
            image_format='png',
            target_width=width,
            quality=92,
        )
        pad_meta = _pad_image_min_height(long_image_path, height)
        meta.update(pad_meta)

        logger.info(f"🎬 FFmpeg 生成滚动视频: {output_filename}")
        _generate_pdf_scroll_video(
            long_image_path=long_image_path,
            output_path=output_path,
            width=width,
            height=height,
            duration=duration,
            start_hold=start_hold,
            end_hold=end_hold,
            fps=fps,
            crf=crf,
        )

        response = send_file(
            output_path,
            as_attachment=True,
            download_name=output_filename,
            mimetype='video/mp4',
        )
        response.headers['X-Page-Count'] = str(meta['page_count'])
        response.headers['X-Long-Image-Width'] = str(meta['width'])
        response.headers['X-Long-Image-Height'] = str(meta['height'])
        response.headers['X-Video-Width'] = str(width)
        response.headers['X-Video-Height'] = str(height)
        response.headers['X-Video-Duration'] = f'{duration:.1f}'
        response.headers['X-File-Size'] = str(os.path.getsize(output_path))
        response.call_on_close(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        return response

    except subprocess.TimeoutExpired:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return jsonify({'success': False, 'message': '视频生成超时，请缩短时长或降低分辨率后重试'}), 504
    except subprocess.CalledProcessError as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        logger.error(f"PDF 滚动视频 FFmpeg 失败: {detail}")
        return jsonify({'success': False, 'message': f'FFmpeg 生成失败: {detail[-500:] or exc}'}), 500
    except Exception as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        logger.error(f"PDF 滚动视频生成失败: {exc}", exc_info=True)
        return jsonify({'success': False, 'message': f'PDF 滚动视频生成失败: {exc}'}), 500

@bp.route('/podcast-visualizer', methods=['POST'])
def generate_podcast_visualizer():
    """
    现代播客视觉化视频。
    路线：模板背景 + 标题透明层 + 音频波形 + 音频 -> H.264 MP4。
    """
    logger.info("📨 收到播客视觉化视频请求")
    temp_dir = tempfile.mkdtemp(prefix='podcast_visualizer_')
    try:
        if shutil.which('ffmpeg') is None or shutil.which('ffprobe') is None:
            return jsonify({'success': False, 'message': '当前环境未找到 FFmpeg/FFprobe，无法生成 MP4'}), 500

        audio = request.files.get('audio')
        if not audio or audio.filename == '':
            return jsonify({'success': False, 'message': '请选择音频文件'}), 400

        audio_ext = os.path.splitext(audio.filename.lower())[1]
        if audio_ext not in PODCAST_AUDIO_EXTENSIONS:
            return jsonify({'success': False, 'message': '音频格式仅支持 mp3、m4a、wav、aac、flac、ogg'}), 400

        audio.seek(0, 2)
        audio_size = audio.tell()
        audio.seek(0)
        if audio_size > PODCAST_AUDIO_MAX_FILE_SIZE:
            return jsonify({
                'success': False,
                'message': f'音频文件过大。最大支持 {get_file_size_str(PODCAST_AUDIO_MAX_FILE_SIZE)}'
            }), 400

        template_file = request.files.get('template')
        background_path = str(PODCAST_TEMPLATE_PATH)
        if template_file and template_file.filename:
            template_ext = os.path.splitext(template_file.filename.lower())[1]
            if template_ext not in {'.png', '.jpg', '.jpeg', '.webp'}:
                return jsonify({'success': False, 'message': '模板图只支持 png、jpg、jpeg、webp'}), 400
            template_file.seek(0, 2)
            template_size = template_file.tell()
            template_file.seek(0)
            if template_size > PODCAST_TEMPLATE_MAX_FILE_SIZE:
                return jsonify({'success': False, 'message': '模板图过大，最大支持 30MB'}), 400
            background_path = os.path.join(temp_dir, f'background{template_ext}')
            template_file.save(background_path)
        elif not os.path.exists(background_path):
            return jsonify({'success': False, 'message': '默认播客模板不存在'}), 500

        try:
            fps = int(request.form.get('fps', '30'))
            crf = int(request.form.get('crf', '20'))
            audio_start = _parse_timecode(request.form.get('audio_start', ''))
            audio_end = _parse_timecode(request.form.get('audio_end', ''))
            audio_speed = _parse_audio_speed(request.form.get('audio_speed', '1'))
        except ValueError:
            return jsonify({'success': False, 'message': '视频或音频参数格式不正确'}), 400

        fps = max(12, min(60, fps))
        crf = max(16, min(32, crf))
        orientation = request.form.get('orientation', 'landscape').strip().lower()
        if orientation == 'portrait':
            width, height = 1080, 1920
        else:
            width, height = 1920, 1080

        title = request.form.get('title', '').strip() or '盘点鲍威尔八年功与过，他给美联储留下了什么？'
        subtitle = request.form.get('subtitle', '').strip() or '2026-05-19 / 精选片段'
        headline_subtitle = request.form.get('headline_subtitle', '').strip()
        brand_text = request.form.get('brand_text', '胶囊播客').strip()
        auto_asr = request.form.get('auto_asr', '').strip().lower() in {'1', 'true', 'yes', 'on'}
        asr_model = request.form.get('asr_model', 'medium').strip() or 'medium'
        srt_file = request.files.get('srt')
        accent_color = request.form.get('accent_color', '#b9895e').strip()

        safe_audio_name = secure_filename(audio.filename) or f'audio{audio_ext}'
        if not os.path.splitext(safe_audio_name)[1]:
            safe_audio_name = f'{safe_audio_name}{audio_ext}'
        audio_path = os.path.join(temp_dir, safe_audio_name)
        audio.save(audio_path)
        processed_audio_path = os.path.join(temp_dir, 'processed_audio.wav')
        audio_meta = _prepare_podcast_audio(
            input_path=audio_path,
            output_path=processed_audio_path,
            start_seconds=audio_start,
            end_seconds=audio_end,
            speed=audio_speed,
        )

        duration = audio_meta['processed_duration']
        overlay_path = os.path.join(temp_dir, 'overlay.png')
        lyrics_ass_path = os.path.join(temp_dir, 'lyrics.ass')
        output_path = os.path.join(temp_dir, 'output.mp4')
        original_base = _safe_base_name(audio.filename)
        output_filename = f"{original_base}.podcast.{orientation}.{int(round(duration))}s.mp4"

        lyrics_segments = []
        if srt_file and srt_file.filename:
            srt_ext = os.path.splitext(srt_file.filename.lower())[1]
            if srt_ext != '.srt':
                return jsonify({'success': False, 'message': '字幕文件只支持 .srt'}), 400
            srt_file.seek(0, 2)
            srt_size = srt_file.tell()
            srt_file.seek(0)
            if srt_size > 5 * 1024 * 1024:
                return jsonify({'success': False, 'message': 'SRT 文件过大，最大支持 5MB'}), 400
            srt_content = srt_file.read().decode('utf-8-sig', errors='ignore')
            lyrics_segments = _retime_segments_for_audio(
                _parse_srt_content(srt_content),
                start_seconds=audio_meta['start_seconds'],
                end_seconds=audio_meta['end_seconds'],
                speed=audio_meta['speed'],
            )
            if not lyrics_segments:
                return jsonify({'success': False, 'message': 'SRT 文件没有解析到有效字幕'}), 400
        elif auto_asr:
            logger.info(f"🎙️ 使用本地 ASR 生成播客字幕: model={asr_model}")
            lyrics_segments = _segments_from_cached_asr(
                audio_path,
                model_size=asr_model,
                start_seconds=audio_meta['start_seconds'],
                end_seconds=audio_meta['end_seconds'],
                speed=audio_meta['speed'],
                prepared_audio_path=processed_audio_path,
            )
        else:
            fallback_caption = request.form.get('caption_text', '').strip()
            if fallback_caption:
                lyrics_segments = [{'start': 0, 'end': min(duration, 6), 'text': fallback_caption}]

        if not lyrics_segments:
            if auto_asr:
                return jsonify({'success': False, 'message': '本地 ASR 没有提取到有效字幕，已停止生成视频'}), 422
            return jsonify({'success': False, 'message': '没有可用字幕：请上传 SRT、开启本地 ASR，或填写手动字幕'}), 400

        _write_podcast_lyrics_ass(lyrics_segments, lyrics_ass_path, width, height)

        logger.info(f"📝 渲染播客标题层: {title}")
        _render_podcast_text_overlay(
            output_path=overlay_path,
            width=width,
            height=height,
            title=title,
            subtitle=subtitle,
            brand_text=brand_text,
            series_text=headline_subtitle,
            accent_color=accent_color,
            caption_text='',
        )

        logger.info(f"🎬 FFmpeg 生成播客视觉化视频: {output_filename}")
        _generate_podcast_visualizer_video(
            background_path=background_path,
            overlay_path=overlay_path,
            audio_path=processed_audio_path,
            output_path=output_path,
            width=width,
            height=height,
            duration=duration,
            fps=fps,
            crf=crf,
            brand_text=brand_text,
            lyrics_ass_path=lyrics_ass_path,
        )

        response = send_file(
            output_path,
            as_attachment=True,
            download_name=output_filename,
            mimetype='video/mp4',
        )
        response.headers['X-Video-Width'] = str(width)
        response.headers['X-Video-Height'] = str(height)
        response.headers['X-Video-Duration'] = f'{duration:.1f}'
        response.headers['X-File-Size'] = str(os.path.getsize(output_path))
        response.headers['X-Lyrics-Count'] = str(len(lyrics_segments))
        response.headers['X-Renderer-Version'] = PODCAST_VISUALIZER_RENDERER_VERSION
        response.call_on_close(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        return response

    except subprocess.TimeoutExpired:
        shutil.rmtree(temp_dir, ignore_errors=True)
        return jsonify({'success': False, 'message': '播客视频生成超时，请降低分辨率或使用更短音频后重试'}), 504
    except subprocess.CalledProcessError as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        logger.error(f"播客视觉化 FFmpeg 失败: {detail}")
        return jsonify({'success': False, 'message': f'FFmpeg 生成失败: {detail[-500:] or exc}'}), 500
    except Exception as exc:
        shutil.rmtree(temp_dir, ignore_errors=True)
        logger.error(f"播客视觉化视频生成失败: {exc}", exc_info=True)
        return jsonify({'success': False, 'message': f'播客视觉化视频生成失败: {exc}'}), 500

@bp.route('/podcast-visualizer/submit', methods=['POST'])
def submit_podcast_visualizer_task():
    from models import db
    from models.task import PublicationTask
    from sqlalchemy.orm.attributes import flag_modified

    try:
        audio = request.files.get('audio')
        if not audio or audio.filename == '':
            return jsonify({'success': False, 'message': '请选择音频文件'}), 400

        audio_ext = os.path.splitext(audio.filename.lower())[1]
        if audio_ext not in PODCAST_AUDIO_EXTENSIONS:
            return jsonify({'success': False, 'message': '音频格式仅支持 mp3、m4a、wav、aac、flac、ogg'}), 400

        srt_file = request.files.get('srt')
        if srt_file and srt_file.filename and os.path.splitext(srt_file.filename.lower())[1] != '.srt':
            return jsonify({'success': False, 'message': '字幕文件只支持 .srt'}), 400

        try:
            audio_start = _parse_timecode(request.form.get('audio_start', ''))
            audio_end = _parse_timecode(request.form.get('audio_end', ''))
            audio_speed = _parse_audio_speed(request.form.get('audio_speed', '1'))
            if audio_start is not None and audio_end is not None and audio_end <= audio_start:
                raise ValueError('截止时间必须晚于开始时间')
            params = {
                'feature': 'podcast_visualizer',
                'podcast_visualizer': {
                    'title': request.form.get('title', '').strip() or '播客标题',
                    'subtitle': request.form.get('subtitle', '').strip(),
                    'headline_subtitle': request.form.get('headline_subtitle', '').strip(),
                    'brand_text': request.form.get('brand_text', '').strip() or '胶囊播客',
                    'caption_text': request.form.get('caption_text', '').strip(),
                    'audio_start': audio_start,
                    'audio_end': audio_end,
                    'audio_speed': audio_speed,
                    'orientation': request.form.get('orientation', 'landscape').strip(),
                    'fps': int(request.form.get('fps', '30')),
                    'crf': int(request.form.get('crf', '20')),
                    'auto_asr': request.form.get('auto_asr', '').strip().lower() in {'1', 'true', 'yes', 'on'},
                    'asr_model': request.form.get('asr_model', 'medium').strip() or 'medium',
                    'renderer_version': PODCAST_VISUALIZER_RENDERER_VERSION,
                }
            }
        except ValueError as exc:
            return jsonify({'success': False, 'message': str(exc)}), 400

        task = PublicationTask(params=params)
        db.session.add(task)
        db.session.commit()

        upload_dir = PROJECT_ROOT / 'data' / 'uploads' / 'podcast_visualizer' / task.id
        upload_dir.mkdir(parents=True, exist_ok=True)
        audio_name = secure_filename(audio.filename) or f'audio{audio_ext}'
        audio_path = upload_dir / audio_name
        audio.save(str(audio_path))
        params['podcast_visualizer']['audio_path'] = str(audio_path)

        if srt_file and srt_file.filename:
            srt_path = upload_dir / (secure_filename(srt_file.filename) or 'lyrics.srt')
            srt_file.save(str(srt_path))
            params['podcast_visualizer']['srt_path'] = str(srt_path)

        task.params = params
        flag_modified(task, 'params')
        db.session.commit()
        try:
            celery_task = _enqueue_podcast_visualizer_task(task)
        except Exception:
            return jsonify({'success': False, 'message': task.error_message}), 502

        return jsonify({
            'success': True,
            'task_id': task.id,
            'celery_id': celery_task.id,
            'message': '播客视频任务已提交'
        })
    except Exception as exc:
        logger.error(f"提交播客视觉化任务失败: {exc}", exc_info=True)
        return jsonify({'success': False, 'message': str(exc)}), 500

@bp.route('/podcast-visualizer/submit-from-path', methods=['POST'])
def submit_podcast_visualizer_task_from_path():
    from models import db
    from models.task import PublicationTask

    try:
        data = request.get_json(silent=True) or {}
        audio_path = str(data.get('audio_path') or '').strip()
        if not audio_path:
            return jsonify({'success': False, 'message': 'audio_path 不能为空'}), 400

        path_obj = Path(audio_path)
        if not path_obj.is_absolute():
            path_obj = PROJECT_ROOT / path_obj
        path_obj = path_obj.resolve()
        project_root = PROJECT_ROOT.resolve()
        try:
            path_obj.relative_to(project_root)
        except ValueError:
            return jsonify({'success': False, 'message': '音频路径必须位于项目目录内'}), 400

        if not path_obj.exists() or not path_obj.is_file():
            return jsonify({'success': False, 'message': '音频文件不存在'}), 400
        if not _is_supported_podcast_audio_path(path_obj):
            return jsonify({'success': False, 'message': '音频格式仅支持 mp3、m4a、wav、aac、flac、ogg'}), 400

        try:
            audio_start = _parse_timecode(data.get('audio_start'))
            audio_end = _parse_timecode(data.get('audio_end'))
            audio_speed = _parse_audio_speed(data.get('audio_speed', 1))
            if audio_start is not None and audio_end is not None and audio_end <= audio_start:
                raise ValueError('截止时间必须晚于开始时间')
            params = {
                'feature': 'podcast_visualizer',
                'podcast_visualizer': {
                    'title': str(data.get('title') or '').strip() or '播客标题',
                    'subtitle': str(data.get('subtitle') or '').strip(),
                    'headline_subtitle': str(data.get('headline_subtitle') or '').strip(),
                    'brand_text': str(data.get('brand_text') or '').strip() or '胶囊播客',
                    'caption_text': str(data.get('caption_text') or '').strip(),
                    'audio_start': audio_start,
                    'audio_end': audio_end,
                    'audio_speed': audio_speed,
                    'orientation': 'landscape',
                    'fps': int(data.get('fps') or 30),
                    'crf': int(data.get('crf') or 20),
                    'auto_asr': bool(data.get('auto_asr', True)),
                    'asr_model': str(data.get('asr_model') or 'medium').strip() or 'medium',
                    'renderer_version': PODCAST_VISUALIZER_RENDERER_VERSION,
                    'audio_path': str(path_obj),
                    'source': 'video_editor',
                }
            }
        except ValueError as exc:
            return jsonify({'success': False, 'message': str(exc)}), 400

        task = PublicationTask(params=params)
        db.session.add(task)
        db.session.commit()
        try:
            celery_task = _enqueue_podcast_visualizer_task(task)
        except Exception:
            return jsonify({'success': False, 'message': task.error_message}), 502

        return jsonify({
            'success': True,
            'task_id': task.id,
            'celery_id': celery_task.id,
            'message': '播客视频任务已提交'
        })
    except Exception as exc:
        logger.error(f"从音频路径提交播客视觉化任务失败: {exc}", exc_info=True)
        return jsonify({'success': False, 'message': str(exc)}), 500

@bp.route('/podcast-visualizer/preview-frame', methods=['POST'])
def preview_podcast_visualizer_frame():
    """
    播客视觉化同源预览帧。
    页面预览和最终视频共用后端标题层、唱片、波形、ASS 字幕与 FFmpeg 合成逻辑。
    """
    temp_dir = None
    try:
        if shutil.which('ffmpeg') is None:
            return jsonify({'success': False, 'message': '当前环境未找到 FFmpeg，无法渲染预览'}), 500
        if not os.path.exists(PODCAST_TEMPLATE_PATH):
            return jsonify({'success': False, 'message': '默认播客模板不存在'}), 500

        payload = request.get_json(silent=True) or {}
        orientation = str(payload.get('orientation') or 'landscape').strip().lower()
        if orientation == 'portrait':
            width, height = 1080, 1920
        else:
            width, height = 1920, 1080

        title = str(payload.get('title') or '').strip() or '盘点鲍威尔八年功与过，他给美联储留下了什么？'
        subtitle = str(payload.get('subtitle') or '').strip() or '2026-05-19 / 精选片段'
        headline_subtitle = str(payload.get('headline_subtitle') or '').strip()
        brand_text = str(payload.get('brand_text') or '').strip() or '胶囊播客'
        caption_text = str(payload.get('caption_text') or '').strip() or '美债收益率持续高企，意味着市场正在重新定价长期资金成本。'
        srt_selected = bool(payload.get('srt_selected'))
        preview_payload = {
            'title': title,
            'subtitle': subtitle,
            'headline_subtitle': headline_subtitle,
            'brand_text': brand_text,
            'caption_text': caption_text,
            'orientation': orientation,
            'srt_selected': srt_selected,
            'width': width,
            'height': height,
        }
        cache_path = _podcast_preview_cache_path(preview_payload)
        if cache_path.exists():
            response = send_file(str(cache_path), mimetype='image/png')
            response.headers['Cache-Control'] = 'private, max-age=3600'
            response.headers['X-Renderer-Version'] = PODCAST_VISUALIZER_RENDERER_VERSION
            response.headers['X-Preview-Cache'] = 'hit'
            return response

        temp_dir = tempfile.mkdtemp(prefix='podcast_visualizer_preview_')
        overlay_path = os.path.join(temp_dir, 'overlay.png')
        lyrics_ass_path = os.path.join(temp_dir, 'lyrics.ass')
        output_path = os.path.join(temp_dir, 'preview.png')

        _render_podcast_text_overlay(
            output_path=overlay_path,
            width=width,
            height=height,
            title=title,
            subtitle=subtitle,
            brand_text=brand_text,
            series_text=headline_subtitle,
            accent_color='#b9895e',
            caption_text='',
        )
        if srt_selected:
            preview_segments = [
                {'start': 0, 'end': 1.6, 'text': '前两句字幕会作为过去内容上移'},
                {'start': 1.6, 'end': 3.0, 'text': '上一句字幕保留在当前句上方'},
                {'start': 3.0, 'end': 5.0, 'text': '已选择 SRT，生成时会使用文件中的真实字幕'},
                {'start': 5.0, 'end': 6.4, 'text': '下一句字幕会提前显示在下方'},
                {'start': 6.4, 'end': 8.0, 'text': '再下一句作为未来内容继续铺垫'},
            ]
        else:
            preview_segments = [
                {'start': 0, 'end': 1.6, 'text': '前两句字幕会作为过去内容上移'},
                {'start': 1.6, 'end': 3.0, 'text': '上一句字幕保留在当前句上方'},
                {'start': 3.0, 'end': 5.0, 'text': caption_text},
                {'start': 5.0, 'end': 6.4, 'text': '下一句字幕会提前显示在下方'},
                {'start': 6.4, 'end': 8.0, 'text': '再下一句作为未来内容继续铺垫'},
            ]
        _write_podcast_lyrics_ass(preview_segments, lyrics_ass_path, width, height)
        _generate_podcast_visualizer_preview_frame(
            background_path=str(PODCAST_TEMPLATE_PATH),
            overlay_path=overlay_path,
            output_path=output_path,
            width=width,
            height=height,
            brand_text=brand_text,
            lyrics_ass_path=lyrics_ass_path,
        )

        PODCAST_PREVIEW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_temp_path = cache_path.with_suffix(f'.{uuid.uuid4().hex}.tmp')
        shutil.copyfile(output_path, cache_temp_path)
        os.replace(cache_temp_path, cache_path)

        response = send_file(str(cache_path), mimetype='image/png')
        response.headers['Cache-Control'] = 'private, max-age=3600'
        response.headers['X-Renderer-Version'] = PODCAST_VISUALIZER_RENDERER_VERSION
        response.headers['X-Preview-Cache'] = 'miss'
        response.call_on_close(lambda: shutil.rmtree(temp_dir, ignore_errors=True))
        return response
    except subprocess.CalledProcessError as exc:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        detail = (exc.stderr or exc.stdout or str(exc)).strip()
        logger.error(f"播客视觉化预览帧 FFmpeg 失败: {detail}")
        return jsonify({'success': False, 'message': f'预览渲染失败: {detail[-500:] or exc}'}), 500
    except Exception as exc:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)
        logger.error(f"播客视觉化预览帧失败: {exc}", exc_info=True)
        return jsonify({'success': False, 'message': f'预览渲染失败: {exc}'}), 500

@bp.route('/podcast-visualizer/tasks', methods=['GET'])
def list_podcast_visualizer_tasks():
    from models.task import PublicationTask

    page = max(1, request.args.get('page', default=1, type=int) or 1)
    per_page = request.args.get('per_page', default=20, type=int) or 20
    per_page = min(50, max(1, per_page))

    recent_tasks = PublicationTask.query.order_by(PublicationTask.created_at.desc()).limit(1000).all()
    tasks = [
        task for task in recent_tasks
        if task.params and task.params.get('feature') == 'podcast_visualizer'
    ]
    total = len(tasks)
    total_pages = max(1, math.ceil(total / per_page))
    page = min(page, total_pages)
    start = (page - 1) * per_page
    end = start + per_page
    page_tasks = tasks[start:end]

    return jsonify({
        'success': True,
        'data': [task.to_dict() for task in page_tasks],
        'pagination': {
            'page': page,
            'per_page': per_page,
            'total': total,
            'total_pages': total_pages,
        },
    })

@bp.route('/podcast-visualizer/requeue/<task_id>', methods=['POST'])
def requeue_podcast_visualizer_task(task_id):
    from models import db
    from models.task import PublicationTask

    try:
        task = PublicationTask.query.get(task_id)
        if not task:
            return jsonify({'success': False, 'message': '任务不存在'}), 404
        if not _is_podcast_visualizer_task(task):
            return jsonify({'success': False, 'message': '只能重新入队播客视觉化任务'}), 400

        task.result = {}
        task.status = 'pending'
        task.current_step = 'init'
        task.progress = 0
        task.error_message = None
        task.completed_at = None
        db.session.commit()

        try:
            celery_task = _enqueue_podcast_visualizer_task(task)
        except Exception:
            return jsonify({'success': False, 'message': task.error_message}), 502

        return jsonify({
            'success': True,
            'message': '播客任务已重新入队',
            'task_id': task.id,
            'celery_id': celery_task.id,
        })
    except Exception as exc:
        logger.error(f"播客任务重新入队失败: {exc}", exc_info=True)
        return jsonify({'success': False, 'message': str(exc)}), 500

@bp.route('/podcast-visualizer/task/<task_id>', methods=['GET'])
def get_podcast_visualizer_task(task_id):
    from models.task import PublicationTask

    task = PublicationTask.query.get(task_id)
    if not task or not task.params or task.params.get('feature') != 'podcast_visualizer':
        return jsonify({'success': False, 'message': '任务不存在'}), 404
    return jsonify({'success': True, 'data': task.to_dict()})

@bp.route('/podcast-visualizer/task/<task_id>/download', methods=['GET'])
def download_podcast_visualizer_task(task_id):
    from models.task import PublicationTask

    task = PublicationTask.query.get(task_id)
    if not task or not task.result or not task.result.get('video_path'):
        return jsonify({'success': False, 'message': '视频不存在'}), 404
    video_path = task.result.get('video_path')
    if not os.path.exists(video_path):
        return jsonify({'success': False, 'message': '视频文件不存在'}), 404
    title = (task.params or {}).get('podcast_visualizer', {}).get('title') or task_id
    return send_file(
        video_path,
        as_attachment=True,
        download_name=f'{_safe_base_name(title)}.podcast.mp4',
        mimetype='video/mp4',
    )
