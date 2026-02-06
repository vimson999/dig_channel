import os
import logging
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps
from typing import Dict, Any, Tuple, Optional

logger = logging.getLogger(__name__)

# --- Constants from Frontend ---
PRESETS = {
    'light': {'name': '☀ 经典浅色', 'main': '#002244', 'sub': '#B8860B', 'tag': '#222222', 'date': '#ffffff', 'stroke': 'rgba(255,255,255,1.0)', 'bg': '#e9ecef', 'isDark': False},
    'dark': {'name': '🌙 经典深色', 'main': '#ffffff', 'sub': '#FFD700', 'tag': '#eeeeee', 'date': '#aaaaaa', 'stroke': 'rgba(0,0,0,0.8)', 'bg': '#1a1a1a', 'isDark': True},
    'morgan': {'name': '🏦 摩根深蓝', 'main': '#002D72', 'sub': '#FFD700', 'tag': '#002D72', 'date': '#ffffff', 'stroke': 'rgba(255,255,255,1.0)', 'bg': '#f0f4f8', 'isDark': False},
    'warm_pro': {'name': '🏛 投行 · 暖色专供', 'main': '#002D72', 'sub': '#C0392B', 'tag': '#002D72', 'date': '#333333', 'stroke': 'rgba(255,255,255,0.95)', 'bg': '#FFF8E1', 'isDark': False},
    'bloomberg': {'name': '📊 彭博风 · 投行流', 'main': '#FFFFFF', 'sub': '#FFA500', 'tag': '#00A4FF', 'date': '#00A4FF', 'stroke': 'rgba(0, 45, 114, 0.8)', 'bg': '#003366', 'isDark': True},
    'gold': {'name': '🏆 黑金奢华', 'main': '#D4AF37', 'sub': '#F5F5F5', 'tag': '#C5A028', 'date': '#999999', 'stroke': 'rgba(0,0,0,0.9)', 'bg': '#000000', 'isDark': True},
    'risk': {'name': '📉 风险警示', 'main': '#C0392B', 'sub': '#2C3E50', 'tag': '#C0392B', 'date': '#ffffff', 'stroke': 'rgba(255,255,255,1.0)', 'bg': '#ecf0f1', 'isDark': False},
    'tech': {'name': '🧬 科技深紫', 'main': '#00FFFF', 'sub': '#FFD700', 'tag': '#D1C4E9', 'date': '#B39DDB', 'stroke': 'rgba(49, 27, 146, 0.8)', 'bg': '#240046', 'isDark': True},
    'clean': {'name': '🌿 清新 · 能源绿', 'main': '#004D40', 'sub': '#2E7D32', 'tag': '#004D40', 'date': '#ffffff', 'stroke': 'rgba(255,255,255,1.0)', 'bg': '#E0F2F1', 'isDark': False},
    'quantum': {'name': '🌌 深空 · 量子灰', 'main': '#ECEFF1', 'sub': '#00B0FF', 'tag': '#B0BEC5', 'date': '#90A4AE', 'stroke': 'rgba(38, 50, 56, 0.8)', 'bg': '#263238', 'isDark': True},
    'vintage': {'name': '🍷 复古 · 华尔街', 'main': '#5D4037', 'sub': '#8D6E63', 'tag': '#3E2723', 'date': '#ffffff', 'stroke': 'rgba(255,248,225,1.0)', 'bg': '#FFF8E1', 'isDark': False}
}

BACKGROUNDS = [
    {'name': '默认 · 几何蓝', 'file': 'Gemini_Generated_Image_ifcvkkifcvkkifcv.png', 'config': {'scheme': 'bloomberg', 'blur': 8, 'mask': 45}},
    {'name': '科技 · 流光紫', 'file': 'Gemini_Generated_Image_5smjm15smjm15smj.png', 'config': {'scheme': 'tech', 'blur': 4, 'mask': 50}},
    {'name': '商务 · 深邃黑', 'file': 'Gemini_Generated_Image_abyrquabyrquabyr.png', 'config': {'scheme': 'dark', 'blur': 8, 'mask': 30}},
    {'name': '抽象 · 波浪线', 'file': 'Gemini_Generated_Image_dmux10dmux10dmux.png', 'config': {'scheme': 'quantum', 'blur': 5, 'mask': 45}},
    {'name': '极简 · 纸张纹', 'file': 'Gemini_Generated_Image_fy2ewzfy2ewzfy2e.png', 'config': {'scheme': 'light', 'blur': 3, 'mask': 20}},
    {'name': '金融 · 上升势', 'file': 'Gemini_Generated_Image_j1dwoqj1dwoqj1dw.png', 'config': {'scheme': 'warm_pro', 'blur': 10, 'mask': 50}},
    {'name': '未来 · 芯片绿', 'file': 'Gemini_Generated_Image_sgk2hbsgk2hbsgk2.png', 'config': {'scheme': 'clean', 'blur': 4, 'mask': 40}},
    {'name': '宏观 · 全球视野', 'file': 'Gemini_Generated_Image_me7j4pme7j4pme7j.png', 'config': {'scheme': 'morgan', 'blur': 6, 'mask': 50}},
    {'name': '市场 · 红色警戒', 'file': 'Gemini_Generated_Image_r98ittr98ittr98i.png', 'config': {'scheme': 'risk', 'blur': 8, 'mask': 60}},
    {'name': '深度 · 蓝海探索', 'file': 'Gemini_Generated_Image_unhywgunhywgunhy.png', 'config': {'scheme': 'morgan', 'blur': 5, 'mask': 40}},
    {'name': '前沿 · 灰色地带', 'file': 'Gemini_Generated_Image_xmotmqxmotmqxmot.png', 'config': {'scheme': 'gold', 'blur': 12, 'mask': 55}}
]

ORGANIZATIONS = [
    {'name': 'Goldman Sachs · 高盛研报', 'color': '#7399C6'},
    {'name': 'Morgan Stanley · 摩根士丹利', 'color': '#002D72'},
    {'name': 'J.P. Morgan · 摩根大通', 'color': '#52433D'},
    {'name': 'CSC · 中信建投', 'color': '#C00000'},
    {'name': 'BlackRock · 贝莱德', 'color': '#000000'},
    {'name': 'Bridgewater · 桥水基金', 'color': '#B31B1B'},
    {'name': 'CITIC Securities · 中信证券', 'color': '#C0392B'},
    {'name': 'CICC · 中金公司', 'color': '#BFA15F'},
    {'name': 'UBS · 瑞银集团', 'color': '#E60000'},
    {'name': 'Credit Suisse · 瑞士信贷', 'color': '#002D72'},
    {'name': 'HSBC · 汇丰银行', 'color': '#DB0011'},
    {'name': 'Sequoia · 红杉资本', 'color': '#2E8B57'},
    {'name': 'Berkshire Hathaway · 伯克希尔', 'color': '#2B3D51'},
    {'name': 'Tesla · 特斯拉', 'color': '#E82127'},
    {'name': 'NVIDIA · 英伟达', 'color': '#76B900'},
    {'name': 'Nomura · 野村证券', 'color': '#EF3340'},
    {'name': 'Bank of America · 美国银行', 'color': '#0061AA'}, # Added
    {'name': 'Deutsche Bank · 德意志银行', 'color': '#0018A8'}  # Added
]

class ThumbnailService:
    def __init__(self, asset_dir=None):
        if asset_dir is None:
            # Determine project root relative to this file
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
            self.asset_dir = os.path.join(base_dir, 'assets', 'cover')
            self.font_base_dir = os.path.join(base_dir, 'assets', 'fonts')
        else:
            self.asset_dir = asset_dir
            self.font_base_dir = os.path.join(os.path.dirname(asset_dir), 'fonts')
            
        self.output_dir = os.path.join(os.path.dirname(self.asset_dir), '..', 'data', 'generated_thumbnails')
        if not os.path.exists(self.output_dir):
            os.makedirs(self.output_dir, exist_ok=True)
            
        # Try to load fonts
        self.font_path = self._find_font()
        logger.info(f"Using font: {self.font_path}")

    def _find_font(self):
        """Find a suitable Chinese font, prioritizing Serif (Songti) style to match frontend."""
        candidates = [
            os.path.join(self.font_base_dir, 'NotoSerifSC-Bold.otf'),
            '/System/Library/Fonts/Supplemental/Songti.ttc', # macOS Catalina+
            '/System/Library/Fonts/Songti.ttc', # macOS older
            '/System/Library/Fonts/PingFang.ttc', # macOS Fallback (Sans)
            '/System/Library/Fonts/STHeiti Light.ttc', # macOS Legacy
            '/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf', # Linux
            'C:\\Windows\\Fonts\\msyh.ttc', # Windows
        ]
        
        for path in candidates:
            if os.path.exists(path):
                return path
        return None 

    def _parse_rgba(self, rgba_str: str) -> Tuple[int, int, int, int]:
        """Parse 'rgba(r,g,b,a)' or hex string to (r,g,b,a) tuple."""
        if rgba_str.startswith('#'):
            return self._hex_to_rgb(rgba_str) + (255,)
            
        try:
            parts = rgba_str.replace('rgba(', '').replace(')', '').split(',')
            r, g, b = int(parts[0]), int(parts[1]), int(parts[2])
            a = float(parts[3]) if len(parts) > 3 else 1.0
            return (r, g, b, int(a * 255))
        except:
            return (0, 0, 0, 255)

    def _hex_to_rgb(self, hex_color: str) -> Tuple[int, int, int]:
        hex_color = hex_color.lstrip('#')
        return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

    def _draw_halo_text(self, draw, text, pos, font, fill_color, stroke_color, stroke_width):
        """Draw text with halo (stroke) using single-pass rendering for better quality."""
        x, y = pos
        # Ensure minimum stroke width for visibility at 1080p
        actual_stroke_width = max(2, int(round(stroke_width)))
        
        # Pillow's text() method supports both fill and stroke in one call
        draw.text(
            (x, y), 
            text, 
            font=font, 
            fill=fill_color, 
            stroke_width=actual_stroke_width, 
            stroke_fill=stroke_color
        )

    def generate_thumbnail(self, 
                          main_title: str, 
                          sub_title: str = "", 
                          date_text: str = "",
                          tag_text: str = "",
                          bg_preset_index: int = 0,
                          layout: str = 'left',
                          output_filename: str = None) -> str:
        """
        Generate a thumbnail image.
        """
        try:
            # 1. Load Config & Assets
            if bg_preset_index < 0 or bg_preset_index >= len(BACKGROUNDS):
                bg_preset_index = 0
            
            bg_config = BACKGROUNDS[bg_preset_index]
            theme_name = bg_config['config']['scheme']
            theme = PRESETS.get(theme_name, PRESETS['morgan'])
            
            # Check 'default' subdirectory first, then root asset_dir
            bg_path_default = os.path.join(self.asset_dir, 'default', bg_config['file'])
            bg_path_root = os.path.join(self.asset_dir, bg_config['file'])
            
            if os.path.exists(bg_path_default):
                bg_path = bg_path_default
            elif os.path.exists(bg_path_root):
                bg_path = bg_path_root
            else:
                logger.warning(f"Background not found: {bg_config['file']} in {self.asset_dir}, using blank canvas.")
                bg_path = None

            if bg_path:
                img = Image.open(bg_path).convert('RGBA')
                img = ImageOps.fit(img, (1920, 1080), method=Image.Resampling.LANCZOS)
            else:
                # Use RGBA for base theme
                img = Image.new('RGB', (1920, 1080), theme['bg']).convert('RGBA')

            # 2. Apply Blur
            blur_amount = bg_config['config'].get('blur', 0)
            if blur_amount > 0:
                img = img.filter(ImageFilter.GaussianBlur(radius=blur_amount))

            # 3. Apply Mask
            mask_alpha = bg_config['config'].get('mask', 40) / 100.0
            if mask_alpha > 0:
                # Create gradient mask for smooth fade
                mask_layer = Image.new('L', (1920, 1080), 0)
                mask_draw = ImageDraw.Draw(mask_layer)
                
                # Determine gradient direction based on layout
                is_dark = theme['isDark']
                base_color_rgb = (0, 0, 0) if is_dark else (0, 15, 30)
                
                # Gradient width: covers 70% of screen
                gradient_width = int(1920 * 0.7)
                
                for x in range(gradient_width):
                    alpha_val = int(255 * mask_alpha * (1 - x / gradient_width))
                    if layout == 'left':
                        mask_draw.line((x, 0, x, 1080), fill=alpha_val)
                    else:
                        mask_draw.line((1920 - x - 1, 0, 1920 - x - 1, 1080), fill=alpha_val)

                # Composite the colored mask layer
                solid_color = Image.new('RGBA', (1920, 1080), base_color_rgb + (255,))
                img.paste(solid_color, (0, 0), mask_layer)

            # 4. Text Drawing Setup
            draw = ImageDraw.Draw(img)
            
            # Coordinates
            base_x = 120 if layout == 'left' else 880
            current_y = 180
            
            # Load Fonts
            try:
                font_tag = ImageFont.truetype(self.font_path, 30)
                font_main = ImageFont.truetype(self.font_path, 110)
                font_sub = ImageFont.truetype(self.font_path, 48)
                font_date = ImageFont.truetype(self.font_path, 32)
            except IOError:
                logger.error("Could not load font, using default.")
                font_tag = font_main = font_sub = font_date = ImageFont.load_default()

            # 4.1 Draw Tag (Rounded Rect + Shadow + Text)
            if tag_text:
                # Find tag color
                tag_color_hex = theme['tag']
                for org in ORGANIZATIONS:
                    if org['name'] == tag_text:
                        tag_color_hex = org['color']
                        break
                
                left, top, right, bottom = draw.textbbox((0, 0), tag_text, font=font_tag)
                text_width = right - left
                text_height = bottom - top
                
                padding_x = 24
                padding_y = 12
                bg_width = text_width + padding_x * 2
                bg_height = 30 + padding_y * 2 
                
                tag_bg_x = base_x - padding_x
                tag_bg_y = current_y - padding_y
                
                # Draw Shadow for Tag
                shadow_layer = Image.new('RGBA', (1920, 1080), (0,0,0,0))
                shadow_draw = ImageDraw.Draw(shadow_layer)
                shadow_draw.rounded_rectangle(
                    (tag_bg_x, tag_bg_y + 4, tag_bg_x + bg_width, tag_bg_y + bg_height + 4),
                    radius=4,
                    fill=(0, 0, 0, 50)
                )
                shadow_layer = shadow_layer.filter(ImageFilter.GaussianBlur(4))
                img.alpha_composite(shadow_layer)
                
                draw = ImageDraw.Draw(img)
                draw.rounded_rectangle(
                    (tag_bg_x, tag_bg_y, tag_bg_x + bg_width, tag_bg_y + bg_height),
                    radius=4,
                    fill='white'
                )
                
                draw.text((base_x, current_y), tag_text, font=font_tag, fill=tag_color_hex)
                current_y += 130

            # 4.2 Draw Main Title
            if main_title:
                lines = main_title.split('\n')
                main_line_height = 110 * 1.25
                stroke_rgba = self._parse_rgba(theme['stroke'])
                main_color = theme['main']
                
                for line in lines:
                    self._draw_halo_text(draw, line, (base_x, current_y), font_main, main_color, stroke_rgba, 110 * 0.06)
                    current_y += main_line_height
                current_y += 10

            # 4.3 Draw Sub Title
            if sub_title:
                stroke_rgba = self._parse_rgba(theme['stroke'])
                sub_color = theme['sub']
                self._draw_halo_text(draw, sub_title, (base_x, current_y), font_sub, sub_color, stroke_rgba, 48 * 0.04)

            # 4.4 Draw Footer (Date)
            if date_text:
                date_x = 120 if layout == 'left' else 1920 - 120
                date_y = 1080 - 100
                
                line_width = 60
                line_x = date_x if layout == 'left' else date_x - line_width
                line_y = date_y - 15 - 32 
                
                draw.line((line_x, line_y, line_x + line_width, line_y), fill=theme['date'], width=1)
                
                date_stroke = (0,0,0,200) if theme['isDark'] else (0,0,0,128)
                
                if layout == 'right':
                    w = draw.textlength(date_text, font=font_date)
                    final_x = date_x - w
                else:
                    final_x = date_x
                    
                self._draw_halo_text(draw, date_text, (final_x, date_y), font_date, theme['date'], date_stroke, 32 * 0.04)

            # 5. Save Output
            if not output_filename:
                import uuid
                output_filename = f"thumbnail_{{uuid.uuid4().hex[:8]}}.jpg"
            
            if not output_filename.lower().endswith('.jpg'):
                output_filename += '.jpg'
                
            output_path = os.path.join(self.output_dir, output_filename)
            # Convert to RGB before saving as JPG
            img.convert('RGB').save(output_path, quality=95)
            logger.info(f"Thumbnail generated: {output_path}")
            
            return output_path

        except Exception as e:
            logger.error(f"Thumbnail generation failed: {e}", exc_info=True)
            raise e

# Singleton
thumbnail_service = ThumbnailService()
