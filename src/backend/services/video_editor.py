import os
import logging
from moviepy import VideoFileClip, AudioFileClip, ImageClip, concatenate_videoclips, CompositeVideoClip
from werkzeug.utils import secure_filename
import uuid
import json
from PIL import Image, ImageDraw, ImageFont

logger = logging.getLogger(__name__)

class VideoEditorService:
    def __init__(self, upload_folder='data/uploads', output_folder='data/processed'):
        # Convert to absolute paths based on project root
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        self.upload_folder = os.path.join(base_dir, upload_folder)
        self.output_folder = os.path.join(base_dir, output_folder)
        
        # Ensure directories exist
        os.makedirs(self.upload_folder, exist_ok=True)
        os.makedirs(self.output_folder, exist_ok=True)

    def _create_cta_image(self, text, style='premium_dark', position='bottom_bar', video_w=1920):
        """Create a multi-line CTA image with transparency and layout support."""
        # Style mapping (matches frontend)
        styles = {
            'premium_dark': {'bg': (25, 28, 36, 217), 'text': (255, 255, 255, 242), 'action': (255, 255, 255, 255), 'border': (255, 255, 255, 38)},
            'nomura_red': {'bg': (128, 20, 30, 225), 'text': (255, 255, 255, 242), 'action': (255, 255, 255, 255), 'border': (255, 255, 255, 50)},
            'institutional_blue': {'bg': (15, 30, 60, 217), 'text': (255, 255, 255, 242), 'action': (255, 255, 255, 255), 'border': (255, 255, 255, 50)},
            'investment_gold': {'bg': (212, 175, 55, 217), 'text': (25, 30, 40, 242), 'action': (0, 0, 0, 255), 'border': (0, 0, 0, 25)}
        }
        s = styles.get(style, styles['premium_dark'])
        is_full_bar = 'bar' in position
        
        lines = [l.strip() for l in text.split('\n') if l.strip()]
        if not lines: lines = ["Call to Action"]

        try:
            font_paths_bold = ["/System/Library/Fonts/Hiragino Sans GB.ttc", "/System/Library/Fonts/PingFang.ttc", "Arial Unicode MS"]
            font_paths_reg = ["/System/Library/Fonts/STHeiti Light.ttc", "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc", "Arial Unicode MS"]
            
            def get_font(size, bold=False):
                paths = font_paths_bold if bold else font_paths_reg
                for p in paths:
                    if os.path.exists(p): return ImageFont.truetype(p, size)
                return ImageFont.load_default()
        except:
            def get_font(size, bold=False): return ImageFont.load_default()

        def get_action_text(txt):
            if txt.startswith('↓') or 'PDF' in txt: return txt
            return "↓ " + txt

        if is_full_bar:
            # --- 【模式 A: 全宽横条 - 左右分栏】 ---
            bw, bh = video_w, 140
            img = Image.new('RGBA', (int(bw), int(bh)), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.rectangle([0, 0, bw, bh], fill=s['bg'])
            if position == 'bottom_bar': draw.line([0, 0, bw, 0], fill=s['border'], width=2)
            else: draw.line([0, bh-2, bw, bh-2], fill=s['border'], width=2)

            h_margin = 100
            left_t = lines[0]
            right_t = lines[1] if len(lines) > 1 else lines[0]
            
            f_l, f_r = get_font(42, False), get_font(48, True)
            action_str = get_action_text(right_t)
            
            l_bbox, r_bbox = f_l.getbbox(left_t), f_r.getbbox(action_str)
            draw.text((h_margin, (bh - (l_bbox[3]-l_bbox[1]))/2 - 4), left_t, font=f_l, fill=s['text'])
            draw.text((bw - h_margin - (r_bbox[2]-r_bbox[0]), (bh - (r_bbox[3]-r_bbox[1]))/2 - 4), 
                      action_str, font=f_r, fill=s['action'])

        else:
            # --- 【模式 B: 悬浮卡片 - 纵向三段式】 ---
            line_metrics = []
            max_w, total_h = 0, 0
            spacing = 25
            
            for i, line in enumerate(lines):
                fs, bold, color = 32, False, s['text']
                if i == 0: fs, bold = 48, True
                elif i == len(lines)-1 and len(lines) > 1: 
                    fs, bold, color = 48, True, s['action']
                    line = get_action_text(line)
                
                f = get_font(fs, bold)
                bbox = f.getbbox(line)
                lw, lh = bbox[2]-bbox[0], bbox[3]-bbox[1]
                if lw > max_w: max_w = lw
                total_h += lh + (spacing if i < len(lines)-1 else 0)
                line_metrics.append({'text': line, 'w': lw, 'h': lh, 'f': f, 'color': color})

            padding_h, padding_v = 80, 60
            bw, bh = max_w + padding_h * 2, total_h + padding_v * 2
            
            # 为阴影预留空间
            sw, sh = 30, 20 # 阴影模糊扩散值和偏移量
            img_w, img_h = bw + sw * 2, bh + sw * 2 + sh
            img = Image.new('RGBA', (int(img_w), int(img_h)), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            
            # 1. 模拟阴影层
            shadow_box = [sw, sw + sh, sw + bw, sw + bh + sh]
            draw.rounded_rectangle(shadow_box, radius=32, fill=(0, 0, 0, 70))
            
            # 2. 绘制主体卡片
            card_box = [sw, sw, sw + bw, sw + bh]
            draw.rounded_rectangle(card_box, radius=32, fill=s['bg'])
            # 3. 精致内描边
            draw.rounded_rectangle(card_box, radius=32, outline=s['border'], width=2)
            
            curr_y = sw + padding_v
            for m in line_metrics:
                lx = sw + (bw - m['w'])/2
                draw.text((lx, curr_y), m['text'], font=m['f'], fill=m['color'])
                curr_y += m['h'] + spacing

        temp_cta_path = os.path.join(self.upload_folder, f"cta_{uuid.uuid4().hex[:6]}.png")
        img.save(temp_cta_path)
        return temp_cta_path

    def _render_intermediate_clip(self, clip, filename, target_fps=24):
        """Helper to render a clip to a temp file and reload it."""
        temp_path = os.path.join(self.output_folder, filename)
        clip.write_videofile(
            temp_path,
            fps=target_fps,
            codec='libx264',
            audio_codec='aac',
            preset='ultrafast',
            threads=4,
            logger=None
        )
        return VideoFileClip(temp_path)

    def _create_subtitle_image(self, text, video_w=1080):
        """Create a subtitle image (Yellow, Bold, Transparent background)."""
        if not text: return None
        
        # 字幕样式配置
        text_color = (255, 215, 0, 255)  # 黄色 #FFD700
        font_size = 72 # 对应竖屏的大号字体
        
        try:
            # 优先使用黑体
            font_paths_bold = ["/System/Library/Fonts/Hiragino Sans GB.ttc", "/System/Library/Fonts/PingFang.ttc", "Arial Unicode MS"]
            font = None
            for p in font_paths_bold:
                if os.path.exists(p): 
                    font = ImageFont.truetype(p, font_size)
                    break
            if not font: font = ImageFont.load_default()
        except:
            font = ImageFont.load_default()

        # 处理换行：每行约 12-15 个汉字
        chars_per_line = 14
        lines = [text[i:i+chars_per_line] for i in range(0, len(text), chars_per_line)]
        
        line_height = font_size * 1.3
        total_h = int(len(lines) * line_height)
        img = Image.new('RGBA', (video_w, total_h + 40), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        for i, line in enumerate(lines):
            bbox = font.getbbox(line)
            lw = bbox[2] - bbox[0]
            lx = (video_w - lw) / 2
            # 增加细微的黑色描边提升可读性
            draw.text((lx-2, i*line_height-2), line, font=font, fill=(0,0,0,180))
            draw.text((lx+2, i*line_height+2), line, font=font, fill=(0,0,0,180))
            draw.text((lx, i*line_height), line, font=font, fill=text_color)

        temp_path = os.path.join(self.upload_folder, f"sub_{uuid.uuid4().hex[:6]}.png")
        img.save(temp_path)
        return temp_path

    def _create_subtitle_clip(self, text, duration, start_time, video_w=1080, position_y=1250):
        """Create a professional subtitle clip (Yellow, Bold, High-Visibility)."""
        if not text: return None
        
        import numpy as np
        font_size = 60 # 调优字号，避免遮挡
        
        # 静态缓存字体对象
        if not hasattr(self, '_cached_font'):
            self._cached_font = None
            font_paths = ["/System/Library/Fonts/Hiragino Sans GB.ttc", "/System/Library/Fonts/PingFang.ttc", "Arial Unicode MS"]
            for p in font_paths:
                if os.path.exists(p): 
                    self._cached_font = ImageFont.truetype(p, font_size)
                    break
            if not self._cached_font: self._cached_font = ImageFont.load_default()

        font = self._cached_font
        chars_per_line = 18 # 字号变小后，每行容纳更多字
        lines = [text[i:i+chars_per_line] for i in range(0, len(text), chars_per_line)]
        line_height = int(font_size * 1.3)
        total_h = len(lines) * line_height + 20
        
        img = Image.new('RGBA', (video_w, total_h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        
        for i, line in enumerate(lines):
            bbox = font.getbbox(line)
            lw = bbox[2] - bbox[0]
            lx = (video_w - lw) / 2
            ly = i * line_height
            
            # 4 方向强描边
            shadow_color = (0, 0, 0, 240)
            for dx, dy in [(-2,-2), (2,-2), (-2,2), (2,2)]:
                draw.text((lx+dx, ly+dy), line, font=font, fill=shadow_color)
            
            # 主文字：亮黄色
            draw.text((lx, ly), line, font=font, fill=(255, 230, 0, 255))

        return ImageClip(np.array(img)).with_duration(duration).with_start(start_time).with_position(('center', position_y))

    def process_vertical_video(self, image_file, audio_files, subtitles=None, output_filename=None):
        """Synthesize a 9:16 vertical video with timed subtitles (Sync Fixed)."""
        raw_resources = []
        try:
            target_w, target_h = 1080, 1920
            target_fps = 24

            audio_paths = audio_files if isinstance(audio_files, list) else [audio_files]
            sub_texts = subtitles if isinstance(subtitles, list) else [subtitles]

            current_time = 0
            subtitle_clips = []
            audio_segments = []

            for i, p in enumerate(audio_paths):
                if not (p and os.path.exists(p)): continue
                segment = AudioFileClip(p)
                audio_segments.append(segment)
                raw_resources.append(segment)
                
                duration = segment.duration
                if i < len(sub_texts) and sub_texts[i]:
                    # 对齐当前音频片段的时间段
                    sub_clip = self._create_subtitle_clip(sub_texts[i], duration, current_time, video_w=target_w, position_y=1250)
                    if sub_clip:
                        subtitle_clips.append(sub_clip)
                        raw_resources.append(sub_clip)
                current_time += duration
            
            if not audio_segments: raise ValueError("No audio.")

            from moviepy import concatenate_audioclips
            audio_final = concatenate_audioclips(audio_segments)
            raw_resources.append(audio_final)

            bg_clip = ImageClip(image_file).with_duration(audio_final.duration).resized(new_size=(target_w, target_h)).with_fps(target_fps)
            # 这里的 CompositeVideoClip 会尊重 sub_clip 的 start 和 duration
            final_video = CompositeVideoClip([bg_clip] + subtitle_clips).with_audio(audio_final)
            raw_resources.append(bg_clip)
            raw_resources.append(final_video)

            output_path = os.path.join(self.output_folder, output_filename or f"v_video_{uuid.uuid4().hex[:8]}.mp4")
            final_video.write_videofile(output_path, codec='libx264', audio_codec='aac', preset='ultrafast', threads=8, logger=None)
            return output_path

        finally:
            for r in raw_resources:
                if hasattr(r, 'close'): r.close()

    def process_mixed_content(self, video_file, image_file, audio_file, video_c_path=None, audio_c_path=None, audio_start=7.0, audio_end_offset=10.0, output_filename=None, cta_config=None, subtitles=None):
        """Synthesize horizontal video with perfect subtitle sync."""
        raw_resources = []
        try:
            clips_to_concat = []
            target_w, target_h = 1920, 1080 
            target_fps = 24 

            # intro
            if video_file and os.path.exists(video_file):
                clip_a = VideoFileClip(video_file)
                if clip_a.w != target_w or clip_a.h != target_h:
                    clip_a = clip_a.resized(new_size=(target_w, target_h))
                raw_resources.append(clip_a)
                clips_to_concat.append(clip_a)
            
            # Process Main Audio & Subtitles
            audio_paths = audio_file if isinstance(audio_file, list) else [audio_file]
            sub_texts = subtitles if isinstance(subtitles, list) else [subtitles]

            audio_segments = []
            subtitle_clips_raw = []
            current_full_time = 0

            for i, p in enumerate(audio_paths):
                if not (isinstance(p, str) and os.path.exists(p)): continue
                segment = AudioFileClip(p)
                audio_segments.append(segment)
                raw_resources.append(segment)
                
                duration = segment.duration
                if i < len(sub_texts) and sub_texts[i]:
                    # 先生成相对于“完整拼接音频”时间轴的字幕
                    sub_clip = self._create_subtitle_clip(sub_texts[i], duration, current_full_time, video_w=target_w, position_y=750)
                    if sub_clip:
                        subtitle_clips_raw.append(sub_clip)
                        raw_resources.append(sub_clip)
                current_full_time += duration
            
            if not audio_segments: raise ValueError("No audio.")
            
            from moviepy import concatenate_audioclips
            audio_b_full = concatenate_audioclips(audio_segments)
            # 根据偏移裁剪音频
            audio_b_clipped = audio_b_full.subclipped(audio_start, max(0, audio_b_full.duration - audio_end_offset))
            target_duration_b = audio_b_clipped.duration
            raw_resources.append(audio_b_full)
            raw_resources.append(audio_b_clipped)

            main_image_clip = ImageClip(image_file).with_duration(target_duration_b).resized(new_size=(target_w, target_h))
            raw_resources.append(main_image_clip)
            
            composition_elements = [main_image_clip]
            
            # --- 关键修复：根据 audio_start 重新对齐所有字幕 ---
            for sc in subtitle_clips_raw:
                # 计算字幕在裁剪后音频中的相对开始时间
                new_start = sc.start - audio_start
                if new_start + sc.duration > 0:
                    actual_start = max(0, new_start)
                    actual_duration = sc.duration - (actual_start - new_start)
                    # 确保字幕时长不会超过裁剪后的正文时长
                    if actual_start < target_duration_b:
                        composition_elements.append(
                            sc.with_start(actual_start).with_duration(min(actual_duration, target_duration_b - actual_start))
                        )

            if cta_config and cta_config.get('text'):
                cta_img_path = self._create_cta_image(cta_config['text'], cta_config.get('style', 'glass_dark'), cta_config.get('position', 'bottom_bar'), video_w=target_w)
                cta_clip = ImageClip(cta_img_path).with_duration(target_duration_b)
                pos_val = cta_config.get('position', 'bottom_bar')
                if 'bar' in pos_val: final_pos = (0, (target_h - cta_clip.h) if 'bottom' in pos_val else 0)
                else:
                    safe_r, safe_b = 120, 220 
                    if pos_val == 'right_center': final_pos = (target_w - cta_clip.w - safe_r, (target_h - cta_clip.h) / 2)
                    else: final_pos = (target_w - cta_clip.w - safe_r, target_h - cta_clip.h - safe_b)
                composition_elements.append(cta_clip.with_position(final_pos))
                raw_resources.append(cta_clip)
                if os.path.exists(cta_img_path): os.remove(cta_img_path)

            clip_b_composed = CompositeVideoClip(composition_elements).with_audio(audio_b_clipped).with_fps(target_fps)
            raw_resources.append(clip_b_composed)
            clips_to_concat.append(clip_b_composed)

            # outro
            if video_c_path and os.path.exists(video_c_path):
                raw_video_c = VideoFileClip(video_c_path)
                if raw_video_c.w != target_w or raw_video_c.h != target_h:
                    raw_video_c = raw_video_c.resized(new_size=(target_w, target_h))
                # ... rest of outro logic
                clips_to_concat.append(raw_video_c)
                raw_resources.append(raw_video_c)

            final_clip = concatenate_videoclips(clips_to_concat)
            output_path = os.path.join(self.output_folder, output_filename or f"video_{uuid.uuid4().hex[:8]}.mp4")
            final_clip.write_videofile(output_path, codec='libx264', audio_codec='aac', preset='ultrafast', threads=8, logger=None)
            return output_path

        finally:
            if 'final_clip' in locals() and final_clip: final_clip.close()
            for r in raw_resources: 
                if hasattr(r, 'close'): r.close()


video_editor_service = VideoEditorService()
