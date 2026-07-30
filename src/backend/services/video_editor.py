import os
import logging
import platform
from moviepy import VideoFileClip, AudioFileClip, ImageClip, concatenate_videoclips, CompositeVideoClip
from werkzeug.utils import secure_filename
import uuid
import json
import datetime
import subprocess
import copy
import re
import shlex
import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

VIDEO_MEDIA_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v'}


def _scheme3_frame_geometry(target_w, target_h):
    """让方案三的 16:9 画面铺满模板内容槽，所有尺寸保持偶数。"""

    frame_x = 0
    frame_w = max(2, target_w)
    frame_w -= frame_w % 2
    frame_h = max(2, round((frame_w * 9 / 16) / 2) * 2)
    frame_y = round(target_h * (620 / 1920))
    return frame_x, frame_y, frame_w, frame_h


class VideoEditorService:
    def __init__(self):
        self.base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        self.upload_folder = os.path.join(self.base_dir, 'data', 'uploads')
        self.output_folder = os.path.join(self.base_dir, 'data', 'processed')
        
        # 针对 Mac M 系列芯片默认使用硬件加速
        self.video_codec = 'h264_videotoolbox' if platform.system() == 'Darwin' else 'libx264'
        logger.info(f"Detected {platform.system()}, using codec: {self.video_codec}")
        self.font_path = self._find_font_path()
        
        os.makedirs(self.upload_folder, exist_ok=True)
        os.makedirs(self.output_folder, exist_ok=True)

    def _ffmpeg_executable(self):
        """Use MoviePy's bundled FFmpeg, which includes the ASS subtitle filter."""
        configured = str(os.environ.get('VIDEO_EDITOR_FFMPEG_BINARY') or '').strip()
        return configured or imageio_ffmpeg.get_ffmpeg_exe()

    def _find_font_path(self):
        candidates = [
            os.path.join(self.base_dir, 'assets', 'fonts', 'SourceHanSansSC-Heavy.otf'),
            '/System/Library/Fonts/PingFang.ttc',
            '/System/Library/Fonts/Hiragino Sans GB.ttc',
            '/System/Library/Fonts/STHeiti Medium.ttc',
            '/System/Library/Fonts/Supplemental/Arial Unicode.ttf',
            '/Library/Fonts/Arial Unicode.ttf',
            '/System/Library/Fonts/Supplemental/Arial.ttf',
            '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf',
        ]
        for path in candidates:
            if path and os.path.exists(path):
                logger.info("Video editor font selected: %s", path)
                return path
        logger.warning("No video editor font found, falling back to PIL default font.")
        return None

    def _generate_srt(self, subtitle_data, audio_base_time=0):
        """Convert timed subtitle data to a standard SRT file string."""
        all_segments = []
        def flatten_extract(data):
            if isinstance(data, dict) and 'text' in data: all_segments.append(data)
            elif isinstance(data, list):
                for item in data: flatten_extract(item)
        
        flatten_extract(subtitle_data)
        if not all_segments: 
            return ""

        def format_time(seconds):
            td = datetime.timedelta(seconds=max(0, seconds))
            total_seconds = int(td.total_seconds())
            hours = total_seconds // 3600
            minutes = (total_seconds % 3600) // 60
            secs = total_seconds % 60
            millis = int(td.microseconds / 1000)
            return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"

        srt_content = ""
        for i, seg in enumerate(all_segments):
            try:
                start = float(seg.get('start', 0)) + audio_base_time
                end = float(seg.get('end', start + 2.0)) + audio_base_time
                text = str(seg.get('text', '')).strip()
                if not text: continue
                srt_content += f"{i+1}\n{format_time(start)} --> {format_time(end)}\n{text}\n\n"
            except: continue
        return srt_content

    def _ass_color(self, value, default):
        """Return an ASS &HAABBGGRR color string from #RRGGBB or an ASS color."""
        text = str(value or "").strip()
        if re.match(r"^&H[0-9A-Fa-f]{8}$", text):
            return text.upper()
        match = re.match(r"^#?([0-9A-Fa-f]{6})$", text)
        if not match:
            return default
        raw = match.group(1)
        rr, gg, bb = raw[0:2], raw[2:4], raw[4:6]
        return f"&H00{bb}{gg}{rr}".upper()

    def _generate_ass(self, srt_content, alignment, margin_v, font_size, play_res_x, play_res_y, subtitle_style=None):
        """Convert SRT content to ASS with explicit PlayRes = video dimensions for exact pixel-level positioning."""
        import re
        subtitle_style = subtitle_style if isinstance(subtitle_style, dict) else {}

        def srt_time_to_ass(t):
            # "HH:MM:SS,mmm" → "H:MM:SS.cc"
            t = t.strip().replace(',', '.')
            parts = t.split(':')
            h, m, rest = parts[0], parts[1], parts[2]
            s, ms = rest.split('.')
            cs = int(ms[:3]) // 10
            return f"{int(h)}:{int(m):02d}:{int(s):02d}.{cs:02d}"

        font_name = str(subtitle_style.get("font_name") or "PingFang SC").replace(",", " ")
        primary_color = self._ass_color(subtitle_style.get("primary_color"), "&H0000FFFF")
        secondary_color = self._ass_color(subtitle_style.get("secondary_color"), "&H000000FF")
        outline_color = self._ass_color(subtitle_style.get("outline_color"), "&H00000000")
        back_color = self._ass_color(subtitle_style.get("back_color"), "&H00000000")
        try:
            outline = float(subtitle_style.get("outline", 2))
        except (TypeError, ValueError):
            outline = 2
        try:
            shadow = float(subtitle_style.get("shadow", 0))
        except (TypeError, ValueError):
            shadow = 0
        try:
            margin_l = int(subtitle_style.get("margin_l", 10))
            margin_r = int(subtitle_style.get("margin_r", 10))
        except (TypeError, ValueError):
            margin_l = margin_r = 10
        wrap_style = subtitle_style.get("wrap_style")
        wrap_line = ""
        if wrap_style is not None:
            try:
                wrap_line = f"WrapStyle: {int(wrap_style)}\n"
            except (TypeError, ValueError):
                wrap_line = ""

        header = (
            f"[Script Info]\n"
            f"ScriptType: v4.00+\n"
            f"PlayResX: {play_res_x}\n"
            f"PlayResY: {play_res_y}\n"
            f"ScaledBorderAndShadow: yes\n"
            f"{wrap_line}"
            f"\n"
            f"[V4+ Styles]\n"
            f"Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            f"Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            f"Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Default,{font_name},{font_size},{primary_color},{secondary_color},{outline_color},{back_color},"
            f"-1,0,0,0,100,100,0,0,1,{outline:g},{shadow:g},{alignment},{margin_l},{margin_r},{margin_v},1\n\n"
            f"[Events]\n"
            f"Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        )

        events = []
        blocks = re.split(r'\n\s*\n', srt_content.strip())
        for block in blocks:
            lines = [l for l in block.strip().split('\n') if l.strip()]
            if len(lines) < 3:
                continue
            # lines[0]=seq, lines[1]=time, lines[2:]=text
            time_line = lines[1]
            if ' --> ' not in time_line:
                continue
            start_t, end_t = time_line.split(' --> ', 1)
            text = r'\N'.join(lines[2:])
            events.append(f"Dialogue: 0,{srt_time_to_ass(start_t)},{srt_time_to_ass(end_t)},Default,,0,0,0,,{text}")

        return header + '\n'.join(events) + '\n'

    def _extract_global_subtitles(self, subtitles, audio_paths, audio_start=0):
        """统一提取并计算带偏移量的全局字幕列表，支持开头偏移量纠正"""
        global_subs = []
        curr_offset = 0

        if not subtitles:
            return []

        # 核心修复：判断是单音频的字幕列表还是多音频的分组列表
        if isinstance(subtitles, list) and len(subtitles) > 0 and isinstance(subtitles[0], dict):
            sub_groups = [subtitles]
        elif isinstance(subtitles, list):
            sub_groups = subtitles
        else:
            sub_groups = [subtitles]

        for i, group in enumerate(sub_groups):
            # 获取对应的音频时长
            duration = 0
            if i < len(audio_paths) and os.path.exists(audio_paths[i]):
                try:
                    from moviepy import AudioFileClip
                    temp_a = AudioFileClip(audio_paths[i])
                    duration = temp_a.duration
                    temp_a.close()
                except: pass

            def extract(d, offset):
                if isinstance(d, dict) and 'text' in d:
                    new_d = copy.deepcopy(d)
                    # 关键修复：应用全局 audio_start 偏移
                    # 每一段字幕的绝对时间 = 原始时间 + 之前所有段的总长 - 开头剪掉的时长
                    new_d['start'] = float(new_d.get('start', 0)) + offset - audio_start
                    new_d['end'] = float(new_d.get('end', 0)) + offset - audio_start

                    # 只保留在视频范围内的字幕
                    if new_d['end'] > 0:
                        new_d['start'] = max(0, new_d['start'])
                        global_subs.append(new_d)
                elif isinstance(d, list):
                    for x in d: extract(x, offset)

            extract(group, curr_offset)
            curr_offset += duration

        return global_subs
    def _create_cta_image(self, text, style='police_yellow', position='bottom_bar', video_w=1920, video_h=1080):
        """Create a multi-line CTA image with transparency and layout support, scaled to video resolution."""
        styles = {
            'premium_dark': {'bg': (25, 28, 36, 217), 'text': (255, 255, 255, 242), 'action': (255, 255, 255, 255), 'border': (255, 255, 255, 38)},
            'nomura_red': {'bg': (184, 20, 33, 230), 'text': (255, 255, 255, 255), 'action': (255, 255, 255, 255), 'border': (255, 255, 255, 50)},
            'police_yellow': {'bg': (255, 204, 0, 240), 'text': (0, 0, 0, 255), 'action': (0, 0, 0, 255), 'border': (0, 0, 0, 80)},
            'glass_dark': {'bg': (0, 0, 0, 160), 'text': (255, 255, 255, 230), 'action': (255, 255, 255, 255), 'border': (255, 255, 255, 30)}
        }
        theme = styles.get(style, styles['police_yellow'])
        text_color = theme['text']
        
        # 根据分辨率缩放基础尺寸
        scale_factor = video_h / 1080.0
        is_bar = 'bar' in position

        raw_lines = [line.strip() for line in text.split('\n') if line.strip()]
        if len(raw_lines) > 3:
            lines = [raw_lines[0], raw_lines[1], ''.join(raw_lines[2:])]
        else:
            lines = raw_lines or [text.strip()]

        def load_font(size):
            if self.font_path:
                try:
                    return ImageFont.truetype(self.font_path, max(12, int(size)))
                except OSError as exc:
                    logger.warning("Failed to load video editor font %s: %s", self.font_path, exc)
            return ImageFont.load_default()

        def text_width(font, line):
            bbox = font.getbbox(line)
            return bbox[2] - bbox[0]

        metrics = []
        if is_bar:
            img_w = video_w
            img_h = max(10, int(140 * scale_factor))
            font_size = int(38 * scale_factor)
            font_main = load_font(font_size)
            line_h = int(45 * scale_factor)
            metrics = [
                {
                    'line': line,
                    'font': font_main,
                    'width': text_width(font_main, line),
                    'height': font_size,
                    'fill': text_color if index == 0 else theme['action'],
                }
                for index, line in enumerate(lines)
            ]
        else:
            max_text_w = min(int(760 * (video_w / 1920.0)), int(video_w * 0.42))
            for index, line in enumerate(lines):
                font_size = int((42 if index == 0 else 34) * scale_factor)
                font = load_font(font_size)
                while font_size > int(24 * scale_factor) and text_width(font, line) > max_text_w:
                    font_size -= max(1, int(2 * scale_factor))
                    font = load_font(font_size)
                metrics.append({
                    'line': line,
                    'font': font,
                    'width': text_width(font, line),
                    'height': font_size,
                    'fill': text_color if index == 0 else theme['action'],
                })

            padding_h = int(60 * (video_w / 1920.0))
            padding_v = int(54 * scale_factor)
            line_gap = int(22 * scale_factor)
            max_line_w = max([item['width'] for item in metrics] + [int(520 * (video_w / 1920.0))])
            total_text_h = sum(item['height'] for item in metrics) + max(0, len(metrics) - 1) * line_gap
            img_w = max(10, max_line_w + padding_h * 2)
            img_h = max(10, total_text_h + padding_v * 2)

        img = Image.new('RGBA', (img_w, img_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        if is_bar:
            draw.rectangle([0, 0, img_w, img_h], fill=theme['bg'])
            draw.line([0, 0, img_w, 0], fill=theme['border'], width=max(1, int(1 * scale_factor)))
            line_gap = int(45 * scale_factor)
            total_text_h = len(metrics) * line_gap
            curr_y = (img_h - total_text_h) / 2
        else:
            draw.rounded_rectangle([0, 0, img_w, img_h], radius=int(20 * scale_factor), fill=theme['bg'], outline=theme['border'], width=max(1, int(2 * scale_factor)))
            line_gap = int(22 * scale_factor)
            curr_y = int(54 * scale_factor)
        
        for item in metrics:
            draw.text(((img_w - item['width']) / 2, curr_y), item['line'], font=item['font'], fill=item['fill'])
            curr_y += item['height'] + line_gap

        temp_path = os.path.join(self.upload_folder, f"cta_{uuid.uuid4().hex[:6]}.png")
        img.save(temp_path)
        return temp_path

    def _compute_subtitle_params(self, is_vertical, margin_v_override=None, subtitle_position=None, canvas_h=1080, subtitle_size='normal'):
        """
        Returns (alignment, margin_v, font_size) for ASS subtitle styling.
        Priority: margin_v_override > subtitle_position > defaults
        """
        size_multiplier = {'small': 0.8, 'normal': 1.0, 'large': 1.25, 'xlarge': 1.5}.get(subtitle_size, 1.0)
        
        # 增加字号，之前太小了。基于高度等比缩放
        if is_vertical:
            font_size = int(56 * (canvas_h / 1920.0) * size_multiplier)
        else:
            font_size = int(48 * (canvas_h / 1080.0) * size_multiplier)
        font_size = max(16, font_size)
        
        logger.info(f"📐 [SUBTITLE_PARAMS] layout={'vertical' if is_vertical else 'horizontal'} | canvas_h={canvas_h} | size={subtitle_size} | margin_v_override={margin_v_override} | subtitle_position={subtitle_position}")

        if margin_v_override is not None:
            # 强制居中底部对齐
            return 2, int(margin_v_override), font_size

        if subtitle_position is not None:
            y_pct = max(0.0, min(1.0, float(subtitle_position.get('y_pct', 0.9))))
            # Alignment=2（底部居中），MarginV = 从底部到文字底边的距离
            alignment = 2
            # 为了让文字“中心”对齐拖动条，减去半个字号的高度
            margin_v = int((1.0 - y_pct) * canvas_h - (font_size / 2.0))
            margin_v = max(0, margin_v)
            logger.info(f"📐 [SUBTITLE_PARAMS] → branch=subtitle_position | y_pct={y_pct:.4f} | alignment={alignment} | margin_v={margin_v} | canvas_h={canvas_h}")
            return alignment, margin_v, font_size

        # 默认值
        alignment, margin_v = (2, int(canvas_h * 0.12)) if is_vertical else (2, int(canvas_h * 0.08))
        logger.info(f"📐 [SUBTITLE_PARAMS] → branch=default | alignment={alignment} | margin_v={margin_v}")
        return alignment, margin_v, font_size

    def _probe_media_duration(self, media_path):
        """Return media duration in seconds using ffprobe."""
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
                    media_path,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            return float(result.stdout.strip())
        except Exception as exc:
            logger.warning(f"⚠️ [FFPROBE] 无法读取媒体时长: {media_path} | {exc}")
            return 0

    def _assert_playable_video(self, media_path):
        """Reject partial MP4 files before they can be published or recovered."""
        try:
            result = subprocess.run(
                [
                    'ffprobe', '-v', 'error', '-select_streams', 'v:0',
                    '-show_entries', 'stream=codec_type,width,height:format=duration',
                    '-of', 'json', media_path,
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=20,
            )
            payload = json.loads(result.stdout or '{}')
            streams = payload.get('streams') if isinstance(payload, dict) else []
            duration = float(((payload.get('format') or {}).get('duration')) or 0)
            valid_stream = any(
                isinstance(stream, dict)
                and stream.get('codec_type') == 'video'
                and int(stream.get('width') or 0) > 0
                and int(stream.get('height') or 0) > 0
                for stream in (streams or [])
            )
            if duration <= 0.1 or not valid_stream:
                raise ValueError('missing video stream or duration')
        except Exception as exc:
            raise RuntimeError(f'视频文件完整性校验失败: {media_path}: {exc}') from exc
        return media_path

    def _synthesize_ffmpeg_direct(self, image_file, audio_file, output_path, subtitles=None, cta_image_path=None, cta_pos=(0,0), is_vertical=False, audio_start=0, audio_duration=None, transform=None, margin_v_override=None, subtitle_position=None, subtitle_size='normal', subtitle_style=None, export_params=None):
        """High-performance synthesis using direct FFmpeg command with customizable export parameters."""
        import time
        s_t = time.time()
        sub_path = None
        output_root, _output_ext = os.path.splitext(output_path)
        working_output_path = f"{output_root}.part-{uuid.uuid4().hex[:8]}.mp4"

        try:
            # 1. 解析导出参数
            export_params = export_params or {}
            res_key = export_params.get('resolution', '1080p').lower()
            
            # 分辨率配置
            if is_vertical:
                res_map = {'480p': (480, 854), '720p': (720, 1280), '1080p': (1080, 1920)}
            else:
                res_map = {'480p': (854, 480), '720p': (1280, 720), '1080p': (1920, 1080)}
            
            target_w, target_h = res_map.get(res_key, (1080, 1920) if is_vertical else (1920, 1080))
            target_res = f"{target_w}:{target_h}"
            
            # 码率配置
            bitrate_map = {'low': '1500k', 'medium': '4000k', 'high': '8000k'}
            v_bitrate = bitrate_map.get(export_params.get('bitrate', 'medium').lower(), '4000k')
            
            # 帧率
            fps = int(export_params.get('fps', 30))

            logger.info(f"⚙️ [EXPORT_CONFIG] Res={target_res} | Bitrate={v_bitrate} | FPS={fps}")

            # 2. 准备字幕 (直接生成带绝对坐标系的 ASS，解决错位问题)
            if subtitles:
                srt_content = self._generate_srt(subtitles, 0)
                if srt_content:
                    alignment, margin_v, font_size = self._compute_subtitle_params(
                        is_vertical, margin_v_override, subtitle_position, canvas_h=target_h, subtitle_size=subtitle_size
                    )
                    if isinstance(subtitle_style, dict) and subtitle_style.get("font_size"):
                        try:
                            font_size = max(16, int(subtitle_style.get("font_size")))
                        except (TypeError, ValueError):
                            pass
                    ass_content = self._generate_ass(
                        srt_content,
                        alignment,
                        margin_v,
                        font_size,
                        target_w,
                        target_h,
                        subtitle_style=subtitle_style,
                    )
                    sub_path = os.path.join(self.upload_folder, f"temp_{uuid.uuid4().hex[:6]}.ass")
                    with open(sub_path, 'w', encoding='utf-8') as f:
                        f.write(ass_content)
                    logger.info(f"📝 [FFMPEG] Generated ASS with PlayRes {target_w}x{target_h} and MarginV {margin_v}")

            # 3. 获取视频时长
            if not audio_duration:
                from moviepy import AudioFileClip
                temp_a = AudioFileClip(audio_file)
                audio_duration = temp_a.duration - audio_start
                temp_a.close()

            # 4. 构建底图滤镜链
            if transform:
                scale = transform.get('scale', 1.0)
                off_x = transform.get('x', 0)
                off_y = transform.get('y', 0)
                vf_chain = f"loop=loop=-1:size=1:start=0,scale={target_res}:force_original_aspect_ratio=increase"
                vf_chain += f",scale=iw*{scale}:ih*{scale}"
                vf_chain += f",crop={target_w}:{target_h}:(iw-{target_w})/2-{off_x}:(ih-{target_h})/2-{off_y},setsar=1"
            else:
                vf_chain = (
                    f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
                    f"crop={target_w}:{target_h}:(iw-ow)/2:0,"
                    f"setsar=1"
                )

            if sub_path:
                safe_sub_path = sub_path.replace(":", "\\:").replace("'", "'\\\\''")
                # 使用 ass 滤镜直接加载我们生成的完美 ASS 文件
                vf_chain += f",ass='{safe_sub_path}'"

            # 5. 输入与参数
            audio_args = f"-ss {audio_start} -t {audio_duration}"
            media_ext = os.path.splitext(str(image_file or ''))[1].lower()
            is_video_background = media_ext in VIDEO_MEDIA_EXTENSIONS
            if is_video_background:
                media_duration = self._probe_media_duration(image_file)
                if media_duration > 0:
                    if media_duration < audio_duration:
                        media_input = f"-stream_loop -1 -t {audio_duration} -i \"{image_file}\""
                        logger.info(
                            f"🎞️ [FFMPEG_EXEC] Background media mode: video loop-to-audio | "
                            f"source={media_duration:.2f}s target={audio_duration:.2f}s"
                        )
                    else:
                        speed_ratio = audio_duration / media_duration
                        media_input = f"-i \"{image_file}\""
                        vf_chain = f"setpts={speed_ratio:.8f}*PTS,{vf_chain}"
                        logger.info(
                            f"🎞️ [FFMPEG_EXEC] Background media mode: video fit-to-audio | "
                            f"source={media_duration:.2f}s target={audio_duration:.2f}s setpts={speed_ratio:.6f}"
                        )
                else:
                    media_input = f"-stream_loop -1 -t {audio_duration} -i \"{image_file}\""
                    logger.info(f"🎞️ [FFMPEG_EXEC] Background media mode: video loop fallback ({os.path.basename(image_file)})")
            else:
                media_input = f"-loop 1 -t {audio_duration} -i \"{image_file}\""
                vf_chain = f"loop=loop=-1:size=1:start=0,{vf_chain}"
                logger.info(f"🖼️ [FFMPEG_EXEC] Background media mode: image ({os.path.basename(image_file)})")

            inputs = [media_input, f"{audio_args} -i \"{audio_file}\""]
            
            aspect_ratio = "9:16" if is_vertical else "16:9"
            res_flag = f"-video_size {target_w}x{target_h} -r {fps}"
            
            if cta_image_path:
                inputs.append(f"-i \"{cta_image_path}\"")
                cta_idx = len(inputs) - 1
                vf_chain += f" [bg]; [{cta_idx}:v] format=rgba [cta]; [bg][cta] overlay={cta_pos[0]}:{cta_pos[1]}"

            ffmpeg_executable = shlex.quote(self._ffmpeg_executable())
            cmd = f"{ffmpeg_executable} -y " + " ".join(inputs) + f" -filter_complex \"{vf_chain}\" -aspect {aspect_ratio} {res_flag} -c:v {self.video_codec} -b:v {v_bitrate} -c:a aac -shortest \"{working_output_path}\""
            
            logger.info(f"🎬 [FFMPEG_EXEC] Mode: {'Vertical' if is_vertical else 'Horizontal'} | Aspect: {aspect_ratio} | Res: {target_w}x{target_h}")
            logger.info(f"🎬 [FFMPEG_EXEC] Command: {cmd}")
            
            try:
                subprocess.run(cmd, shell=True, check=True, capture_output=True)
            except subprocess.CalledProcessError as e:
                stderr = e.stderr.decode(errors='ignore') if e.stderr else ''
                if self.video_codec == 'h264_videotoolbox' and 'h264_videotoolbox' in stderr:
                    fallback_cmd = cmd.replace('-c:v h264_videotoolbox', '-c:v libx264 -preset medium')
                    logger.warning("⚠️ [FFMPEG_EXEC] Hardware encoder unavailable, retrying with libx264")
                    subprocess.run(fallback_cmd, shell=True, check=True, capture_output=True)
                else:
                    raise
            self._assert_playable_video(working_output_path)
            os.replace(working_output_path, output_path)
            logger.info(f"✅ [FFMPEG_EXEC] Success: {os.path.basename(output_path)}")
            return output_path

        except Exception as e:
            logger.error(f"❌ [FFMPEG_DIRECT] Synthesis failed: {e}")
            detail = ""
            if hasattr(e, 'stderr') and e.stderr:
                detail = e.stderr.decode(errors='ignore').strip()
                logger.error(f"FFmpeg Stderr: {detail}")
            summary = detail.splitlines()[-1] if detail else str(e)
            raise RuntimeError(f"FFmpeg 视频合成失败: {summary}") from e
        finally:
            if sub_path and os.path.exists(sub_path): os.remove(sub_path)
            if os.path.exists(working_output_path):
                try: os.remove(working_output_path)
                except OSError: pass

    def process_vertical_video(self, image_file, audio_files, subtitles=None, output_filename=None, transform=None, margin_v_override=None, subtitle_position=None, subtitle_size='normal', subtitle_style=None, export_params=None, duration_limit=None):
        """Synthesize a 9:16 vertical video with correct sequential subtitles."""
        logger.info(f"🎥 [VIDEO_EDITOR] Starting Vertical Production: {os.path.basename(image_file)}")
        
        output_path = os.path.join(self.output_folder, output_filename or f"v_video_{uuid.uuid4().hex[:6]}.mp4")
        temp_audio = os.path.join(self.upload_folder, f"temp_v_a_{uuid.uuid4().hex[:6]}.mp3")
        
        try:
            audio_list = audio_files if isinstance(audio_files, list) else [audio_files]
            
            # 1. 统一提取全局对齐字幕
            global_subs = self._extract_global_subtitles(subtitles, audio_list)
            logger.info(f"📊 [VIDEO_EDITOR] Extracted {len(global_subs)} total subtitle segments for vertical")

            # 2. 合并音频
            from moviepy import AudioFileClip, concatenate_audioclips
            clips = [AudioFileClip(a) for a in audio_list if os.path.exists(a)]
            if not clips: raise ValueError("No valid audio clips.")
            
            final_audio = concatenate_audioclips(clips)
            if duration_limit:
                limit = max(0.1, float(duration_limit))
                if getattr(final_audio, 'duration', 0) > limit:
                    if hasattr(final_audio, 'subclipped'):
                        final_audio = final_audio.subclipped(0, limit)
                    else:
                        final_audio = final_audio.subclip(0, limit)
            final_audio.write_audiofile(temp_audio, logger=None)
            for c in clips: c.close()
            final_audio.close()

            # 3. 调用合成
            return self._synthesize_ffmpeg_direct(
                image_file=image_file,
                audio_file=temp_audio,
                output_path=output_path,
                subtitles=global_subs,
                is_vertical=True,
                transform=transform,
                margin_v_override=margin_v_override,
                subtitle_position=subtitle_position,
                subtitle_size=subtitle_size,
                subtitle_style=subtitle_style,
                export_params=export_params
            )
        finally:
            if os.path.exists(temp_audio): os.remove(temp_audio)

    def process_framed_vertical_video(
        self,
        background_image,
        content_video,
        audio_files,
        subtitles=None,
        output_filename=None,
        subtitle_position=None,
        subtitle_size='normal',
        subtitle_style=None,
        export_params=None,
        duration_limit=None,
    ):
        """Embed a 16:9 visual video in the workbench 9:16 ``video_bg`` frame."""
        if not content_video or not os.path.isfile(content_video):
            raise ValueError("方案三缺少已完成的视觉视频")
        output_path = os.path.join(
            self.output_folder,
            output_filename or f"v_framed_{uuid.uuid4().hex[:6]}.mp4",
        )
        temp_audio = os.path.join(self.upload_folder, f"temp_vf_a_{uuid.uuid4().hex[:6]}.mp3")
        try:
            audio_list = audio_files if isinstance(audio_files, list) else [audio_files]
            global_subs = self._extract_global_subtitles(subtitles, audio_list)
            from moviepy import AudioFileClip, concatenate_audioclips
            clips = [AudioFileClip(path) for path in audio_list if os.path.exists(path)]
            if not clips:
                raise ValueError("No valid audio clips.")
            final_audio = concatenate_audioclips(clips)
            if duration_limit:
                limit = max(0.1, float(duration_limit))
                if getattr(final_audio, "duration", 0) > limit:
                    final_audio = (
                        final_audio.subclipped(0, limit)
                        if hasattr(final_audio, "subclipped")
                        else final_audio.subclip(0, limit)
                    )
            final_audio.write_audiofile(temp_audio, logger=None)
            for clip in clips:
                clip.close()
            final_audio.close()
            return self._synthesize_framed_vertical_ffmpeg(
                background_image=background_image,
                content_video=content_video,
                audio_file=temp_audio,
                output_path=output_path,
                subtitles=global_subs,
                subtitle_position=subtitle_position,
                subtitle_size=subtitle_size,
                subtitle_style=subtitle_style,
                export_params=export_params,
            )
        finally:
            if os.path.exists(temp_audio):
                os.remove(temp_audio)

    def process_framed_vertical_scenes(
        self,
        background_image,
        scenes,
        audio_files,
        subtitles=None,
        output_filename=None,
        subtitle_position=None,
        subtitle_size='normal',
        subtitle_style=None,
        export_params=None,
        duration_limit=None,
    ):
        """Render scheme3 directly from scene images and whole audio in one encode."""
        scene_items = [
            dict(item)
            for item in (scenes or [])
            if isinstance(item, dict) and item.get("image_path")
        ]
        if not scene_items:
            raise ValueError("方案三缺少已完成的正文分镜图片")
        for item in scene_items:
            if not os.path.isfile(str(item.get("image_path") or "")):
                raise ValueError(f"方案三第 {item.get('sequence')} 段图片不存在")

        output_path = os.path.join(
            self.output_folder,
            output_filename or f"v_framed_scenes_{uuid.uuid4().hex[:6]}.mp4",
        )
        temp_audio = os.path.join(self.upload_folder, f"temp_vfs_a_{uuid.uuid4().hex[:6]}.mp3")
        try:
            audio_list = audio_files if isinstance(audio_files, list) else [audio_files]
            global_subs = self._extract_global_subtitles(subtitles, audio_list)
            valid_audio_paths = [path for path in audio_list if os.path.exists(path)]
            if len(valid_audio_paths) == 1 and not duration_limit:
                return self._synthesize_framed_vertical_scenes_ffmpeg(
                    background_image=background_image,
                    scenes=scene_items,
                    audio_file=valid_audio_paths[0],
                    output_path=output_path,
                    subtitles=global_subs,
                    subtitle_position=subtitle_position,
                    subtitle_size=subtitle_size,
                    subtitle_style=subtitle_style,
                    export_params=export_params,
                )
            from moviepy import AudioFileClip, concatenate_audioclips
            clips = [AudioFileClip(path) for path in valid_audio_paths]
            if not clips:
                raise ValueError("No valid audio clips.")
            final_audio = concatenate_audioclips(clips)
            if duration_limit:
                limit = max(0.1, float(duration_limit))
                if getattr(final_audio, "duration", 0) > limit:
                    final_audio = (
                        final_audio.subclipped(0, limit)
                        if hasattr(final_audio, "subclipped")
                        else final_audio.subclip(0, limit)
                    )
            final_audio.write_audiofile(temp_audio, logger=None)
            for clip in clips:
                clip.close()
            final_audio.close()
            return self._synthesize_framed_vertical_scenes_ffmpeg(
                background_image=background_image,
                scenes=scene_items,
                audio_file=temp_audio,
                output_path=output_path,
                subtitles=global_subs,
                subtitle_position=subtitle_position,
                subtitle_size=subtitle_size,
                subtitle_style=subtitle_style,
                export_params=export_params,
            )
        finally:
            if os.path.exists(temp_audio):
                os.remove(temp_audio)

    def _synthesize_framed_vertical_scenes_ffmpeg(
        self,
        *,
        background_image,
        scenes,
        audio_file,
        output_path,
        subtitles=None,
        subtitle_position=None,
        subtitle_size='normal',
        subtitle_style=None,
        export_params=None,
    ):
        """Compose scene stills inside the scheme3 frame without an intermediate MP4."""
        export_params = export_params or {}
        res_key = str(export_params.get("resolution") or "1080p").lower()
        target_w, target_h = {
            "480p": (480, 854),
            "720p": (720, 1280),
            "1080p": (1080, 1920),
        }.get(res_key, (1080, 1920))
        frame_x, frame_y, frame_w, frame_h = _scheme3_frame_geometry(target_w, target_h)
        fps = int(export_params.get("fps") or 30)
        bitrate = {
            "low": "1500k",
            "medium": "4000k",
            "high": "8000k",
        }.get(str(export_params.get("bitrate") or "medium").lower(), "4000k")
        audio_duration = self._probe_media_duration(audio_file)
        if audio_duration <= 0:
            raise ValueError("方案三音频时长无效")

        raw_durations = []
        for item in scenes:
            try:
                duration = float(item.get("duration_seconds") or 0)
            except (TypeError, ValueError):
                duration = 0
            raw_durations.append(max(0.001, duration))
        total_duration = sum(raw_durations)
        if total_duration <= 0:
            raise ValueError("方案三分镜时长无效")

        normalized_scenes = []
        cursor = 0.0
        if audio_duration < total_duration - 0.5:
            # A preview uses the beginning of the real timeline; it must not
            # squeeze every scene into the shorter preview audio.
            for item, raw_duration in zip(scenes, raw_durations):
                if cursor >= audio_duration - 0.001:
                    break
                duration = min(raw_duration, audio_duration - cursor)
                normalized_scenes.append({**item, "duration_seconds": duration})
                cursor += duration
        else:
            # Full renders may differ by a few milliseconds after MP3 muxing.
            # Normalize that tiny discrepancy so video and audio end together.
            for item, raw_duration in zip(scenes, raw_durations):
                duration = audio_duration * raw_duration / total_duration
                if cursor >= audio_duration - 0.001:
                    break
                duration = min(duration, audio_duration - cursor)
                normalized_scenes.append({**item, "duration_seconds": duration})
                cursor += duration
        if normalized_scenes and abs(cursor - audio_duration) > 0.001:
            normalized_scenes[-1]["duration_seconds"] += audio_duration - cursor
        if not normalized_scenes:
            raise ValueError("方案三没有可合成的正文分镜")

        # Quantize cumulative scene boundaries to frames. Quantizing every
        # scene independently inside FFmpeg can otherwise lose almost one
        # frame per cut and truncate the final audio on long storyboards.
        target_frames = max(1, round(audio_duration * fps))
        previous_frame = 0
        cumulative_seconds = 0.0
        for index, item in enumerate(normalized_scenes):
            cumulative_seconds += float(item["duration_seconds"])
            end_frame = (
                target_frames
                if index == len(normalized_scenes) - 1
                else max(previous_frame + 1, round(cumulative_seconds * fps))
            )
            end_frame = min(target_frames, end_frame)
            item["duration_seconds"] = (end_frame - previous_frame) / fps
            previous_frame = end_frame

        sub_path = None
        output_root, _ = os.path.splitext(output_path)
        working_output_path = f"{output_root}.part-{uuid.uuid4().hex[:8]}.mp4"
        try:
            filters = [f"[0:v]scale={target_w}:{target_h},setsar=1[bg]"]
            concat_inputs = []
            for index, item in enumerate(normalized_scenes, start=1):
                duration = float(item["duration_seconds"])
                filters.append(
                    f"[{index}:v]scale={frame_w}:{frame_h}:force_original_aspect_ratio=increase,"
                    f"crop={frame_w}:{frame_h},setsar=1,fps={fps},format=yuv420p,"
                    f"trim=duration={duration:.6f},setpts=PTS-STARTPTS[scene{index}]"
                )
                concat_inputs.append(f"[scene{index}]")
            filters.append(
                f"{''.join(concat_inputs)}concat=n={len(normalized_scenes)}:v=1:a=0[content]"
            )
            filters.append(f"[bg][content]overlay={frame_x}:{frame_y}:shortest=1[composed]")
            output_label = "composed"
            if subtitles:
                srt_content = self._generate_srt(subtitles, 0)
                if srt_content:
                    alignment, margin_v, font_size = self._compute_subtitle_params(
                        True,
                        subtitle_position=subtitle_position,
                        canvas_h=target_h,
                        subtitle_size=subtitle_size,
                    )
                    if isinstance(subtitle_style, dict) and subtitle_style.get("font_size"):
                        font_size = max(16, int(subtitle_style["font_size"]))
                    ass_content = self._generate_ass(
                        srt_content,
                        alignment,
                        margin_v,
                        font_size,
                        target_w,
                        target_h,
                        subtitle_style=subtitle_style,
                    )
                    sub_path = os.path.join(self.upload_folder, f"temp_{uuid.uuid4().hex[:6]}.ass")
                    with open(sub_path, "w", encoding="utf-8") as handle:
                        handle.write(ass_content)
                    safe_sub_path = sub_path.replace(":", "\\:").replace("'", "'\\\\''")
                    filters.append(f"[composed]ass='{safe_sub_path}'[vout]")
                    output_label = "vout"

            ffmpeg = self._ffmpeg_executable()
            command = [
                ffmpeg, "-y",
                "-loop", "1", "-framerate", str(fps),
                "-i", background_image,
            ]
            for item in normalized_scenes:
                command.extend([
                    "-loop", "1",
                    "-framerate", str(fps),
                    "-i", str(item["image_path"]),
                ])
            audio_input = len(normalized_scenes) + 1
            command.extend([
                "-i", audio_file,
                "-filter_complex", ";".join(filters),
                "-map", f"[{output_label}]",
                "-map", f"{audio_input}:a:0",
                "-r", str(fps),
                "-c:v", self.video_codec,
                "-b:v", bitrate,
                "-pix_fmt", "yuv420p",
                "-c:a", "aac",
                "-shortest",
                working_output_path,
            ])
            try:
                subprocess.run(command, check=True, capture_output=True)
            except subprocess.CalledProcessError as exc:
                stderr = exc.stderr.decode(errors="ignore") if exc.stderr else ""
                if self.video_codec == "h264_videotoolbox" and "h264_videotoolbox" in stderr:
                    fallback = list(command)
                    codec_index = fallback.index("h264_videotoolbox")
                    fallback[codec_index:codec_index + 1] = ["libx264", "-preset", "medium"]
                    subprocess.run(fallback, check=True, capture_output=True)
                else:
                    detail = stderr.splitlines()[-1] if stderr else str(exc)
                    raise RuntimeError(f"方案三分镜直出 FFmpeg 合成失败: {detail}") from exc
            self._assert_playable_video(working_output_path)
            os.replace(working_output_path, output_path)
            return output_path
        finally:
            if sub_path and os.path.exists(sub_path):
                os.remove(sub_path)
            if os.path.exists(working_output_path):
                os.remove(working_output_path)

    def _synthesize_framed_vertical_ffmpeg(
        self,
        *,
        background_image,
        content_video,
        audio_file,
        output_path,
        subtitles=None,
        subtitle_position=None,
        subtitle_size='normal',
        subtitle_style=None,
        export_params=None,
    ):
        """Render scheme3 with the visual content constrained to the template frame."""
        export_params = export_params or {}
        res_key = str(export_params.get("resolution") or "1080p").lower()
        target_w, target_h = {
            "480p": (480, 854),
            "720p": (720, 1280),
            "1080p": (1080, 1920),
        }.get(res_key, (1080, 1920))
        frame_x, frame_y, frame_w, frame_h = _scheme3_frame_geometry(target_w, target_h)
        fps = int(export_params.get("fps") or 30)
        bitrate = {
            "low": "1500k",
            "medium": "4000k",
            "high": "8000k",
        }.get(str(export_params.get("bitrate") or "medium").lower(), "4000k")
        audio_duration = self._probe_media_duration(audio_file)
        if audio_duration <= 0:
            raise ValueError("方案三音频时长无效")

        sub_path = None
        output_root, _ = os.path.splitext(output_path)
        working_output_path = f"{output_root}.part-{uuid.uuid4().hex[:8]}.mp4"
        try:
            filters = [
                f"[0:v]scale={target_w}:{target_h},setsar=1[bg]",
                (
                    f"[1:v]scale={frame_w}:{frame_h}:force_original_aspect_ratio=increase,"
                    f"crop={frame_w}:{frame_h},setsar=1[content]"
                ),
                f"[bg][content]overlay={frame_x}:{frame_y}:shortest=1[composed]",
            ]
            output_label = "composed"
            if subtitles:
                srt_content = self._generate_srt(subtitles, 0)
                if srt_content:
                    alignment, margin_v, font_size = self._compute_subtitle_params(
                        True,
                        subtitle_position=subtitle_position,
                        canvas_h=target_h,
                        subtitle_size=subtitle_size,
                    )
                    if isinstance(subtitle_style, dict) and subtitle_style.get("font_size"):
                        font_size = max(16, int(subtitle_style["font_size"]))
                    ass_content = self._generate_ass(
                        srt_content,
                        alignment,
                        margin_v,
                        font_size,
                        target_w,
                        target_h,
                        subtitle_style=subtitle_style,
                    )
                    sub_path = os.path.join(self.upload_folder, f"temp_{uuid.uuid4().hex[:6]}.ass")
                    with open(sub_path, "w", encoding="utf-8") as handle:
                        handle.write(ass_content)
                    safe_sub_path = sub_path.replace(":", "\\:").replace("'", "'\\\\''")
                    filters.append(f"[composed]ass='{safe_sub_path}'[vout]")
                    output_label = "vout"

            ffmpeg = self._ffmpeg_executable()
            command = [
                ffmpeg, "-y",
                "-loop", "1", "-t", str(audio_duration), "-i", background_image,
                "-stream_loop", "-1", "-t", str(audio_duration), "-i", content_video,
                "-i", audio_file,
                "-filter_complex", ";".join(filters),
                "-map", f"[{output_label}]",
                "-map", "2:a:0",
                "-r", str(fps),
                "-c:v", self.video_codec,
                "-b:v", bitrate,
                "-c:a", "aac",
                "-shortest",
                working_output_path,
            ]
            try:
                subprocess.run(command, check=True, capture_output=True)
            except subprocess.CalledProcessError as exc:
                stderr = exc.stderr.decode(errors="ignore") if exc.stderr else ""
                if self.video_codec == "h264_videotoolbox" and "h264_videotoolbox" in stderr:
                    fallback = list(command)
                    codec_index = fallback.index("h264_videotoolbox")
                    fallback[codec_index:codec_index + 1] = ["libx264", "-preset", "medium"]
                    subprocess.run(fallback, check=True, capture_output=True)
                else:
                    raise RuntimeError(f"方案三 FFmpeg 合成失败: {stderr.splitlines()[-1] if stderr else exc}") from exc
            self._assert_playable_video(working_output_path)
            os.replace(working_output_path, output_path)
            return output_path
        finally:
            if sub_path and os.path.exists(sub_path):
                os.remove(sub_path)
            if os.path.exists(working_output_path):
                os.remove(working_output_path)

    def process_mixed_content(self, video_file, image_file, audio_file, video_c_path=None, audio_c_path=None, audio_start=7.0, audio_end_offset=10.0, output_filename=None, cta_config=None, subtitles=None, transform=None, subtitle_position=None, subtitle_size='normal', export_params=None):
        """Synthesize horizontal video with intro/outro and sequential subtitles."""
        logger.info(f"🎥 [VIDEO_EDITOR] Starting Horizontal Production: {os.path.basename(image_file)}")
        
        # 默认分辨率用于预览和 CTA 计算，实际合成会参考 export_params
        target_w, target_h = 1920, 1080
        if export_params and export_params.get('resolution') == '480p':
            target_w, target_h = 854, 480
        elif export_params and export_params.get('resolution') == '720p':
            target_w, target_h = 1280, 720

        temp_audio = os.path.join(self.upload_folder, f"temp_h_a_{uuid.uuid4().hex[:6]}.mp3")
        temp_body = os.path.join(self.upload_folder, f"temp_h_body_{uuid.uuid4().hex[:6]}.mp4")
        cta_img = None
        working_final_path = None
        
        try:
            audio_list = audio_file if isinstance(audio_file, list) else [audio_file]
            
            # 1. 统一提取全局对齐字幕 (直接传入 audio_start 统一处理)
            global_subs = self._extract_global_subtitles(subtitles, audio_list, audio_start=audio_start)
            
            from moviepy import AudioFileClip, concatenate_audioclips
            clips = [AudioFileClip(a) for a in audio_list if os.path.exists(a)]
            merged_audio = concatenate_audioclips(clips)
            merged_audio.write_audiofile(temp_audio, logger=None)
            
            # 计算切分后的正文时长
            body_duration = max(0.1, merged_audio.duration - audio_start - audio_end_offset)
            
            for c in clips: c.close()
            merged_audio.close()

            # 2. 准备 CTA
            cta_pos = (0, 0)
            if cta_config and cta_config.get('text'):
                cta_img = self._create_cta_image(cta_config['text'], cta_config.get('style', 'police_yellow'), cta_config.get('position', 'bottom_bar'), video_w=target_w, video_h=target_h)
                pos_val = cta_config.get('position', 'bottom_bar')
                with Image.open(cta_img) as cta_probe:
                    cta_w, cta_h = cta_probe.size
                if 'bar' in pos_val: cta_pos = (0, (target_h - cta_h) if 'bottom' in pos_val else 0)
                else:
                    if pos_val == 'right_center': cta_pos = (target_w - cta_w - int(120*(target_w/1920)), (target_h - cta_h) / 2)
                    else: cta_pos = (target_w - cta_w - int(120*(target_w/1920)), target_h - cta_h - int(220*(target_h/1080)))

            # 3. 合成正文
            self._synthesize_ffmpeg_direct(
                image_file=image_file, audio_file=temp_audio, output_path=temp_body,
                subtitles=global_subs, cta_image_path=cta_img, cta_pos=cta_pos,
                audio_start=audio_start, audio_duration=body_duration,
                transform=transform, subtitle_position=subtitle_position,
                subtitle_size=subtitle_size,
                export_params=export_params
            )

            # 4. 拼接
            final_clips = []
            if video_file and os.path.exists(video_file):
                v_in = VideoFileClip(video_file)
                final_clips.append(v_in.resize(newsize=(target_w, target_h)) if v_in.w != target_w else v_in)
            
            final_clips.append(VideoFileClip(temp_body))
            
            if video_c_path and os.path.exists(video_c_path):
                v_out = VideoFileClip(video_c_path)
                final_clips.append(v_out.resize(newsize=(target_w, target_h)) if v_out.w != target_w else v_out)

            final_path = os.path.join(self.output_folder, output_filename or f"video_{uuid.uuid4().hex[:8]}.mp4")
            final_root, _final_ext = os.path.splitext(final_path)
            working_final_path = f"{final_root}.part-{uuid.uuid4().hex[:8]}.mp4"
            
            if len(final_clips) > 0:
                if len(final_clips) == 1:
                    # The body already contains the final audio/video streams.
                    # Re-encoding it through MoviePy doubles runtime and leaves
                    # a much larger interruption window for long audio jobs.
                    final_clips[0].close()
                    self._assert_playable_video(temp_body)
                    os.replace(temp_body, final_path)
                    return final_path
                concat = concatenate_videoclips(final_clips) if len(final_clips) > 1 else final_clips[0]
                # 这里也应用码率和帧率控制
                fps = int(export_params.get('fps', 30)) if export_params else 30
                bitrate_map = {'low': '1500k', 'medium': '4000k', 'high': '8000k'}
                v_bitrate = bitrate_map.get(export_params.get('bitrate', 'medium').lower(), '4000k') if export_params else '4000k'
                
                concat.write_videofile(working_final_path, codec=self.video_codec, audio_codec='aac', fps=fps, bitrate=v_bitrate, logger=None)
                self._assert_playable_video(working_final_path)
                
                for c in final_clips: c.close()
                os.replace(working_final_path, final_path)
            
            return final_path

        finally:
            for f in [temp_audio, temp_body, cta_img, working_final_path]:
                if f and os.path.exists(f): 
                    try: os.remove(f)
                    except: pass

video_editor_service = VideoEditorService()
