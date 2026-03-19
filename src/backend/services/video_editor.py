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
    def _create_cta_image(self, text, style='premium_dark', position='bottom_bar', video_w=1920):
        """Create a multi-line CTA image with transparency and layout support."""
        styles = {
            'premium_dark': {'bg': (25, 28, 36, 217), 'text': (255, 255, 255, 242), 'action': (255, 255, 255, 255), 'border': (255, 255, 255, 38)},
            'nomura_red': {'bg': (184, 20, 33, 230), 'text': (255, 255, 255, 255), 'action': (255, 255, 255, 255), 'border': (255, 255, 255, 50)},
            'glass_dark': {'bg': (0, 0, 0, 160), 'text': (255, 255, 255, 230), 'action': (255, 255, 255, 255), 'border': (255, 255, 255, 30)}
        }
        theme = styles.get(style, styles['premium_dark'])
        text_color = theme['text']
        
        is_bar = 'bar' in position
        img_w = video_w if is_bar else 600
        img_h = 140 if is_bar else 280
        
        img = Image.new('RGBA', (img_w, img_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        if is_bar:
            draw.rectangle([0, 0, img_w, img_h], fill=theme['bg'])
            draw.line([0, 0, img_w, 0], fill=theme['border'], width=1)
        else:
            draw.rounded_rectangle([0, 0, img_w, img_h], radius=20, fill=theme['bg'], outline=theme['border'], width=2)

        try:
            font_path = "/System/Library/Fonts/PingFang.ttc"
            font_main = ImageFont.truetype(font_path, 32 if is_bar else 38)
        except:
            font_main = ImageFont.load_default()

        lines = text.split('\n')
        total_text_h = len(lines) * 45
        curr_y = (img_h - total_text_h) / 2
        
        for line in lines:
            bbox = font_main.getbbox(line)
            lw = bbox[2] - bbox[0]
            draw.text(((img_w - lw)/2, curr_y), line, font=font_main, fill=text_color)
            curr_y += 45

        temp_path = os.path.join(self.upload_folder, f"cta_{uuid.uuid4().hex[:6]}.png")
        img.save(temp_path)
        return temp_path

    def _synthesize_ffmpeg_direct(self, image_file, audio_file, output_path, subtitles=None, cta_image_path=None, cta_pos=(0,0), is_vertical=False, audio_start=0, audio_duration=None, transform=None, margin_v_override=None):
        """High-performance synthesis using direct FFmpeg command."""
        import time
        s_t = time.time()
        srt_path = None
        
        try:
            # 1. 准备字幕 SRT
            # ... (rest of SRT prep unchanged) ...
            if subtitles:
                srt_content = self._generate_srt(subtitles, 0)
                if srt_content:
                    srt_path = os.path.join(self.upload_folder, f"temp_{uuid.uuid4().hex[:6]}.srt")
                    with open(srt_path, 'w', encoding='utf-8') as f:
                        f.write(srt_content)
                    logger.info(f"📝 [FFMPEG] Generated SRT with {srt_content.count('-->')} segments")

            # 2. 获取视频时长
            if not audio_duration:
                from moviepy import AudioFileClip
                temp_a = AudioFileClip(audio_file)
                audio_duration = temp_a.duration - audio_start
                temp_a.close()

            # 3. 构建底图滤镜链 (支持平移与缩放)
            target_w, target_h = (1080, 1920) if is_vertical else (1920, 1080)
            target_res = f"{target_w}:{target_h}"
            
            if transform:
                scale = transform.get('scale', 1.0)
                off_x = transform.get('x', 0)
                off_y = transform.get('y', 0)
                
                # 复杂的滤镜链实现：先 scale 成覆盖态，再应用用户自定义缩放，最后用带偏移量的 crop
                vf_chain = f"loop=loop=-1:size=1:start=0,scale={target_res}:force_original_aspect_ratio=increase"
                vf_chain += f",scale=iw*{scale}:ih*{scale}"
                vf_chain += f",crop={target_w}:{target_h}:(iw-{target_w})/2-{off_x}:(ih-{target_h})/2-{off_y},setsar=1"
            else:
                # 默认逻辑优化：
                # 竖版视频 (is_vertical=True) 通常是截图，使用 force_original_aspect_ratio=decrease 防止裁剪
                # 横屏视频 (is_vertical=False) 通常是背景，使用 force_original_aspect_ratio=increase 确保填满
                fit_mode = "decrease" if is_vertical else "increase"
                vf_chain = f"loop=loop=-1:size=1:start=0,scale={target_res}:force_original_aspect_ratio={fit_mode}"
                if is_vertical:
                    # 如果是缩小适配，需要补黑边居中
                    vf_chain += f",pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2"
                else:
                    # 如果是放大适配，需要裁剪
                    vf_chain += f",crop={target_res}"
                vf_chain += ",setsar=1"

            if srt_path:
                # 针对 Mac 优化路径转义：使用双引号并转义冒号
                safe_srt_path = srt_path.replace(":", "\\:").replace("'", "'\\\\''")
                
                if is_vertical:
                    # 📱 竖屏样式：黄色 Bold 加粗，位置进一步下移 (MarginV 198, 从顶向下)
                    font_size = 10
                    margin_v = margin_v_override if margin_v_override is not None else 198
                    alignment = 6
                    # PrimaryColour: &H0000FFFF (纯黄)
                    style = f"FontName=PingFang SC,FontSize={font_size},PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Alignment={alignment},MarginV={margin_v},Bold=1"
                else:
                    # 🖥️ 横屏样式：纯黄 Bold (去掉底框)，位置下移 (MarginV 38, 从底向上)
                    # BorderStyle=1 (无底框), PrimaryColour=&H0000FFFF (纯黄)
                    font_size = 20
                    margin_v = margin_v_override if margin_v_override is not None else 38
                    alignment = 2
                    style = f"FontName=PingFang SC,FontSize={font_size},PrimaryColour=&H0000FFFF,OutlineColour=&H00000000,BorderStyle=1,Outline=2,Alignment={alignment},MarginV={margin_v},Bold=1"
                
                vf_chain += f",subtitles='{safe_srt_path}':force_style='{style}'"

            # 4. 输入与参数
            audio_args = f"-ss {audio_start} -t {audio_duration}"
            inputs = [f"-loop 1 -t {audio_duration} -i \"{image_file}\"", f"{audio_args} -i \"{audio_file}\""]
            
            if cta_image_path:
                inputs.append(f"-i \"{cta_image_path}\"")
                cta_idx = len(inputs) - 1
                vf_chain += f" [bg]; [{cta_idx}:v] format=rgba [cta]; [bg][cta] overlay={cta_pos[0]}:{cta_pos[1]}"

            cmd = f"ffmpeg -y " + " ".join(inputs) + f" -filter_complex \"{vf_chain}\" -c:v {self.video_codec} -b:v 6000k -c:a aac -shortest \"{output_path}\""
            
            logger.info(f"🚀 [FFMPEG_DIRECT] Executing: {cmd}")
            # 使用 check=True 确保报错能被捕获
            subprocess.run(cmd, shell=True, check=True, capture_output=True)
            logger.info(f"✅ [FFMPEG_DIRECT] Synthesis success in {time.time()-s_t:.2f}s")
            return output_path

        except Exception as e:
            logger.error(f"❌ [FFMPEG_DIRECT] Synthesis failed: {e}")
            if hasattr(e, 'stderr') and e.stderr:
                logger.error(f"FFmpeg Stderr: {e.stderr.decode()}")
            return None
        finally:
            if srt_path and os.path.exists(srt_path): os.remove(srt_path)

    def process_vertical_video(self, image_file, audio_files, subtitles=None, output_filename=None, transform=None, margin_v_override=None):
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
                margin_v_override=margin_v_override
            )
        finally:
            if os.path.exists(temp_audio): os.remove(temp_audio)

    def process_mixed_content(self, video_file, image_file, audio_file, video_c_path=None, audio_c_path=None, audio_start=7.0, audio_end_offset=10.0, output_filename=None, cta_config=None, subtitles=None, transform=None):
        """Synthesize horizontal video with intro/outro and sequential subtitles."""
        logger.info(f"🎥 [VIDEO_EDITOR] Starting Horizontal Production: {os.path.basename(image_file)}")
        
        target_w, target_h = 1920, 1080
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
                cta_img = self._create_cta_image(cta_config['text'], cta_config.get('style', 'premium_dark'), cta_config.get('position', 'bottom_bar'), video_w=target_w)
                pos_val = cta_config.get('position', 'bottom_bar')
                cta_h = 140 if 'bar' in pos_val else 280 
                cta_w = target_w if 'bar' in pos_val else 600
                if 'bar' in pos_val: cta_pos = (0, (target_h - cta_h) if 'bottom' in pos_val else 0)
                else:
                    if pos_val == 'right_center': cta_pos = (target_w - cta_w - 120, (target_h - cta_h) / 2)
                    else: cta_pos = (target_w - cta_w - 120, target_h - cta_h - 220)

            # 3. 合成正文
            self._synthesize_ffmpeg_direct(
                image_file=image_file, audio_file=temp_audio, output_path=temp_body,
                subtitles=adjusted_subs, cta_image_path=cta_img, cta_pos=cta_pos,
                audio_start=audio_start, audio_duration=body_duration,
                transform=transform
            )

            # 4. 拼接
            final_clips = []
            if video_file and os.path.exists(video_file):
                v_in = VideoFileClip(video_file)
                final_clips.append(v_in.resized(new_size=(target_w, target_h)) if v_in.w != target_w else v_in)
            
            final_clips.append(VideoFileClip(temp_body))
            
            if video_c_path and os.path.exists(video_c_path):
                v_out = VideoFileClip(video_c_path)
                final_clips.append(v_out.resized(new_size=(target_w, target_h)) if v_out.w != target_w else v_out)

            final_path = os.path.join(self.output_folder, output_filename or f"video_{uuid.uuid4().hex[:8]}.mp4")
            if len(final_clips) > 1:
                concat = concatenate_videoclips(final_clips)
                concat.write_videofile(final_path, codec=self.video_codec, audio_codec='aac', logger=None)
                for c in final_clips: c.close()
            else:
                import shutil
                shutil.move(temp_body, final_path)
            
            return final_path

        finally:
            for f in [temp_audio, temp_body, cta_img]:
                if f and os.path.exists(f): 
                    try: os.remove(f)
                    except: pass

video_editor_service = VideoEditorService()
