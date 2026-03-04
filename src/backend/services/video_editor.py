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

    def process_vertical_video(self, image_file, audio_files, output_filename=None):
        """Synthesize a pure 9:16 vertical video from an image and audio segments."""
        raw_resources = []
        try:
            target_w, target_h = 1080, 1920
            target_fps = 24

            # 1. Process Audio
            audio_paths = []
            if isinstance(audio_files, list): audio_paths = audio_files
            elif isinstance(audio_files, str) and audio_files.startswith('['):
                import json
                try: audio_paths = json.loads(audio_files)
                except: audio_paths = [audio_files]
            else: audio_paths = [audio_files] if audio_files else []

            audio_segments = []
            for p in audio_paths:
                if p and os.path.exists(p):
                    segment = AudioFileClip(p)
                    audio_segments.append(segment)
                    raw_resources.append(segment)
            
            if not audio_segments:
                raise ValueError("No valid audio files for vertical video.")

            if len(audio_segments) > 1:
                from moviepy import concatenate_audioclips
                audio_final = concatenate_audioclips(audio_segments)
                raw_resources.append(audio_final)
            else:
                audio_final = audio_segments[0]

            # 2. Process Image to Video
            duration = audio_final.duration
            video_clip = ImageClip(image_file).with_duration(duration).resized(new_size=(target_w, target_h)).with_fps(target_fps).with_audio(audio_final)
            
            output_path = os.path.join(self.output_folder, output_filename or f"v_video_{uuid.uuid4().hex[:8]}.mp4")
            video_clip.write_videofile(output_path, codec='libx264', audio_codec='aac', remove_temp=True, logger=None)
            
            return output_path

        finally:
            for r in raw_resources:
                if hasattr(r, 'close'): r.close()

    def process_mixed_content(self, video_file, image_file, audio_file, video_c_path=None, audio_c_path=None, audio_start=7.0, audio_end_offset=10.0, output_filename=None, cta_config=None):
        """Synthesize video with advanced CTA placement. Supports multiple audio segments."""
        raw_resources = []
        intermediate_clips = []
        final_clip = None
        temp_files_to_clean = []

        try:
            clips_to_concat = []
            target_w, target_h = 1920, 1080 
            target_fps = 24 

            if video_file and os.path.exists(video_file):
                clip_a = VideoFileClip(video_file)
                raw_resources.append(clip_a)
                target_w, target_h = clip_a.w, clip_a.h
                target_fps = getattr(clip_a, 'fps', 24) or 24
                clips_to_concat.append(clip_a)
            
            if video_c_path:
                raw_video_c = VideoFileClip(video_c_path)
                raw_resources.append(raw_video_c)
                if audio_c_path:
                    audio_c = AudioFileClip(audio_c_path)
                    raw_resources.append(audio_c)
                    target_duration_c = audio_c.duration
                    clip_c_base = raw_video_c.loop(duration=target_duration_c) if raw_video_c.duration < target_duration_c else raw_video_c.subclipped(0, target_duration_c)
                    clip_c_composed = clip_c_base.with_duration(target_duration_c).with_audio(audio_c).resized(new_size=(target_w, target_h)).with_fps(target_fps)
                else:
                    clip_c_composed = raw_video_c.resized(new_size=(target_w, target_h)).with_fps(target_fps)
                
                temp_c_name = f"temp_c_{uuid.uuid4().hex[:6]}.mp4"
                clip_c_final = self._render_intermediate_clip(clip_c_composed, temp_c_name, target_fps)
                intermediate_clips.append(clip_c_final)
                clips_to_concat.append(clip_c_final)
                temp_files_to_clean.append(os.path.join(self.output_folder, temp_c_name))
                clip_c_composed.close()

            # Process Audio B (Support Multiple)
            audio_paths = []
            if isinstance(audio_file, list):
                audio_paths = audio_file
            elif isinstance(audio_file, str) and audio_file.strip().startswith('['):
                try:
                    audio_paths = json.loads(audio_file)
                except:
                    audio_paths = [audio_file]
            else:
                audio_paths = [audio_file] if audio_file else []

            # Ensure all paths in audio_paths are strings and exist
            audio_segments = []
            for p in audio_paths:
                if isinstance(p, str) and os.path.exists(p):
                    segment = AudioFileClip(p)
                    audio_segments.append(segment)
                    raw_resources.append(segment)
                elif p:
                    logger.warning(f"Audio path not found or invalid: {p}")
            
            if not audio_segments:
                raise ValueError(f"No valid audio files found in: {audio_paths}")
            
            if len(audio_segments) > 1:
                from moviepy import concatenate_audioclips
                logger.info(f"Concatenating {len(audio_segments)} audio segments...")
                audio_b_raw = concatenate_audioclips(audio_segments)
                raw_resources.append(audio_b_raw)
            else:
                audio_b_raw = audio_segments[0]

            audio_b_clipped = audio_b_raw.subclipped(audio_start, max(0, audio_b_raw.duration - audio_end_offset))
            target_duration_b = audio_b_clipped.duration
            
            main_image_clip = ImageClip(image_file).with_duration(target_duration_b).resized(new_size=(target_w, target_h))
            
            if cta_config and cta_config.get('text'):
                cta_pos_val = cta_config.get('position', 'bottom_bar')
                cta_img_path = self._create_cta_image(cta_config['text'], cta_config.get('style', 'glass_dark'), cta_pos_val, video_w=target_w)
                temp_files_to_clean.append(cta_img_path)
                cta_clip = ImageClip(cta_img_path).with_duration(target_duration_b)
                
                if 'bar' in cta_pos_val:
                    final_pos = (0, (target_h - cta_clip.h) if 'bottom' in cta_pos_val else 0)
                else:
                    safe_r, safe_b = 120, 220 # 20% safe zone
                    if cta_pos_val == 'right_center':
                        final_pos = (target_w - cta_clip.w - safe_r, (target_h - cta_clip.h) / 2)
                    else: # bottom_right
                        final_pos = (target_w - cta_clip.w - safe_r, target_h - cta_clip.h - safe_b)
                
                cta_clip = cta_clip.with_position(final_pos)
                clip_b_composed = CompositeVideoClip([main_image_clip, cta_clip]).with_audio(audio_b_clipped).with_fps(target_fps)
                raw_resources.append(cta_clip)
            else:
                clip_b_composed = main_image_clip.with_audio(audio_b_clipped).with_fps(target_fps)

            temp_b_name = f"temp_b_{uuid.uuid4().hex[:6]}.mp4"
            clip_b_final = self._render_intermediate_clip(clip_b_composed, temp_b_name, target_fps)
            intermediate_clips.append(clip_b_final)
            clips_to_concat.append(clip_b_final)
            temp_files_to_clean.append(os.path.join(self.output_folder, temp_b_name))
            clip_b_composed.close()
            main_image_clip.close()
            
            final_clip = concatenate_videoclips(clips_to_concat)
            output_path = os.path.join(self.output_folder, output_filename or f"video_{uuid.uuid4().hex[:8]}.mp4")
            final_clip.write_videofile(output_path, codec='libx264', audio_codec='aac', remove_temp=True, logger=None)
            return output_path

        finally:
            if final_clip: final_clip.close()
            for c in intermediate_clips: c.close()
            for r in raw_resources: 
                if hasattr(r, 'close'): r.close()
            for p in temp_files_to_clean:
                if os.path.exists(p): os.remove(p)

video_editor_service = VideoEditorService()
