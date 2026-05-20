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
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

VIDEO_MEDIA_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.m4v'}

class VideoEditorService:
    def __init__(self):
        self.base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        self.upload_folder = os.path.join(self.base_dir, 'data', 'uploads')
        self.output_folder = os.path.join(self.base_dir, 'data', 'processed')
        
        # 针对 Mac M 系列芯片默认使用硬件加速
        self.video_codec = 'h264_videotoolbox' if platform.system() == 'Darwin' else 'libx264'
        logger.info(f"Detected {platform.system()}, using codec: {self.video_codec}")
        
        os.makedirs(self.upload_folder, exist_ok=True)
        os.makedirs(self.output_folder, exist_ok=True)

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

    def _generate_ass(self, srt_content, alignment, margin_v, font_size, play_res_x, play_res_y):
        """Convert SRT content to ASS with explicit PlayRes = video dimensions for exact pixel-level positioning."""
        import re

        def srt_time_to_ass(t):
            # "HH:MM:SS,mmm" → "H:MM:SS.cc"
            t = t.strip().replace(',', '.')
            parts = t.split(':')
            h, m, rest = parts[0], parts[1], parts[2]
            s, ms = rest.split('.')
            cs = int(ms[:3]) // 10
            return f"{int(h)}:{int(m):02d}:{int(s):02d}.{cs:02d}"

        header = (
            f"[Script Info]\n"
            f"ScriptType: v4.00+\n"
            f"PlayResX: {play_res_x}\n"
            f"PlayResY: {play_res_y}\n"
            f"ScaledBorderAndShadow: yes\n\n"
            f"[V4+ Styles]\n"
            f"Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            f"Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            f"Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Default,PingFang SC,{font_size},&H0000FFFF,&H000000FF,&H00000000,&H00000000,"
            f"-1,0,0,0,100,100,0,0,1,2,0,{alignment},10,10,{margin_v},1\n\n"
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
    def _create_cta_image(self, text, style='premium_dark', position='bottom_bar', video_w=1920, video_h=1080):
        """Create a multi-line CTA image with transparency and layout support, scaled to video resolution."""
        styles = {
            'premium_dark': {'bg': (25, 28, 36, 217), 'text': (255, 255, 255, 242), 'action': (255, 255, 255, 255), 'border': (255, 255, 255, 38)},
            'nomura_red': {'bg': (184, 20, 33, 230), 'text': (255, 255, 255, 255), 'action': (255, 255, 255, 255), 'border': (255, 255, 255, 50)},
            'police_yellow': {'bg': (255, 204, 0, 240), 'text': (0, 0, 0, 255), 'action': (0, 0, 0, 255), 'border': (0, 0, 0, 80)},
            'glass_dark': {'bg': (0, 0, 0, 160), 'text': (255, 255, 255, 230), 'action': (255, 255, 255, 255), 'border': (255, 255, 255, 30)}
        }
        theme = styles.get(style, styles['premium_dark'])
        text_color = theme['text']
        
        # 根据分辨率缩放基础尺寸
        scale_factor = video_h / 1080.0
        is_bar = 'bar' in position
        
        img_w = video_w if is_bar else int(600 * (video_w / 1920.0))
        img_h = int(140 * scale_factor) if is_bar else int(280 * scale_factor)
        
        # 避免尺寸过小
        img_w = max(10, img_w)
        img_h = max(10, img_h)

        img = Image.new('RGBA', (img_w, img_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        if is_bar:
            draw.rectangle([0, 0, img_w, img_h], fill=theme['bg'])
            draw.line([0, 0, img_w, 0], fill=theme['border'], width=max(1, int(1 * scale_factor)))
        else:
            draw.rounded_rectangle([0, 0, img_w, img_h], radius=int(20 * scale_factor), fill=theme['bg'], outline=theme['border'], width=max(1, int(2 * scale_factor)))

        try:
            font_path = "/System/Library/Fonts/PingFang.ttc"
            # 缩放字体
            font_size = int((32 if is_bar else 38) * scale_factor)
            font_main = ImageFont.truetype(font_path, max(12, font_size))
        except:
            font_main = ImageFont.load_default()

        lines = text.split('\n')
        line_h = int(45 * scale_factor)
        total_text_h = len(lines) * line_h
        curr_y = (img_h - total_text_h) / 2
        
        for line in lines:
            bbox = font_main.getbbox(line)
            lw = bbox[2] - bbox[0]
            draw.text(((img_w - lw)/2, curr_y), line, font=font_main, fill=text_color)
            curr_y += line_h

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
        alignment, margin_v = (2, int(canvas_h * 0.15)) if is_vertical else (2, int(canvas_h * 0.08))
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

    def _synthesize_ffmpeg_direct(self, image_file, audio_file, output_path, subtitles=None, cta_image_path=None, cta_pos=(0,0), is_vertical=False, audio_start=0, audio_duration=None, transform=None, margin_v_override=None, subtitle_position=None, subtitle_size='normal', export_params=None):
        """High-performance synthesis using direct FFmpeg command with customizable export parameters."""
        import time
        s_t = time.time()
        sub_path = None

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
                    ass_content = self._generate_ass(srt_content, alignment, margin_v, font_size, target_w, target_h)
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

            cmd = f"ffmpeg -y " + " ".join(inputs) + f" -filter_complex \"{vf_chain}\" -aspect {aspect_ratio} {res_flag} -c:v {self.video_codec} -b:v {v_bitrate} -c:a aac -shortest \"{output_path}\""
            
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
            logger.info(f"✅ [FFMPEG_EXEC] Success: {os.path.basename(output_path)}")
            return output_path

        except Exception as e:
            logger.error(f"❌ [FFMPEG_DIRECT] Synthesis failed: {e}")
            if hasattr(e, 'stderr') and e.stderr:
                logger.error(f"FFmpeg Stderr: {e.stderr.decode()}")
            return None
        finally:
            if sub_path and os.path.exists(sub_path): os.remove(sub_path)

    def process_vertical_video(self, image_file, audio_files, subtitles=None, output_filename=None, transform=None, margin_v_override=None, subtitle_position=None, subtitle_size='normal', export_params=None):
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
                export_params=export_params
            )
        finally:
            if os.path.exists(temp_audio): os.remove(temp_audio)

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
                cta_img = self._create_cta_image(cta_config['text'], cta_config.get('style', 'premium_dark'), cta_config.get('position', 'bottom_bar'), video_w=target_w, video_h=target_h)
                pos_val = cta_config.get('position', 'bottom_bar')
                cta_h = int(140 * (target_h / 1080)) if 'bar' in pos_val else int(280 * (target_h / 1080))
                cta_w = target_w if 'bar' in pos_val else int(600 * (target_w / 1920))
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
            
            if len(final_clips) > 0:
                concat = concatenate_videoclips(final_clips) if len(final_clips) > 1 else final_clips[0]
                # 这里也应用码率和帧率控制
                fps = int(export_params.get('fps', 30)) if export_params else 30
                bitrate_map = {'low': '1500k', 'medium': '4000k', 'high': '8000k'}
                v_bitrate = bitrate_map.get(export_params.get('bitrate', 'medium').lower(), '4000k') if export_params else '4000k'
                
                concat.write_videofile(final_path, codec=self.video_codec, audio_codec='aac', fps=fps, bitrate=v_bitrate, logger=None)
                
                if len(final_clips) > 1:
                    for c in final_clips: c.close()
                else:
                    final_clips[0].close()
            
            return final_path

        finally:
            for f in [temp_audio, temp_body, cta_img]:
                if f and os.path.exists(f): 
                    try: os.remove(f)
                    except: pass

video_editor_service = VideoEditorService()
