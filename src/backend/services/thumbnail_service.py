import os
import logging
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps
from typing import Dict, Any, Tuple, Optional

logger = logging.getLogger(__name__)

# --- Constants ---
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
    {'name': 'NVIDIA · 英英达', 'color': '#76B900'},
    {'name': 'Nomura · 野村证券', 'color': '#EF3340'},
    {'name': 'Bank of America · 美国银行', 'color': '#0061AA'}, 
    {'name': 'Deutsche Bank · 德意志银行', 'color': '#0018A8'},  
    {'name': 'Bernstein · 伯恩斯坦', 'color': '#002C77'},  
    {'name': '新闻速读', 'color': '#E60000'}
]

class ThumbnailService:
    def __init__(self, asset_dir=None):
        if asset_dir is None:
            base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
            self.asset_dir = os.path.join(base_dir, 'assets', 'cover')
            self.font_base_dir = os.path.join(base_dir, 'assets', 'fonts')
        else:
            self.asset_dir = asset_dir
            self.font_base_dir = os.path.join(os.path.dirname(asset_dir), 'fonts')
        project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
        self.output_dir = os.path.join(project_root, 'data', 'generated_thumbnails')
        if not os.path.exists(self.output_dir): os.makedirs(self.output_dir, exist_ok=True)
        self.font_path = self._find_font()
        logger.info(f"Using font: {self.font_path}")

    def _find_font(self):
        candidates = [
            os.path.join(self.font_base_dir, 'SourceHanSansSC-Heavy.otf'),
            '/System/Library/Fonts/PingFang.ttc', 
            '/usr/share/fonts/truetype/source-han-sans/SourceHanSansSC-Heavy.otf',
            'C:\\Windows\\Fonts\\msyhbd.ttc'
        ]
        for path in candidates:
            if os.path.exists(path): return path
        return None 

    def _parse_rgba(self, rgba_str: str) -> Tuple[int, int, int, int]:
        if rgba_str.startswith('#'): return self._hex_to_rgb(rgba_str) + (255,)
        try:
            parts = rgba_str.replace('rgba(', '').replace(')', '').split(',')
            r, g, b = int(parts[0]), int(parts[1]), int(parts[2])
            a = float(parts[3]) if len(parts) > 3 else 1.0
            return (r, g, b, int(a * 255))
        except: return (0, 0, 0, 255)

    def _hex_to_rgb(self, hex_color: str) -> Tuple[int, int, int]:
        hex_color = hex_color.lstrip('#')
        return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))

    def _draw_halo_text(self, draw, text, pos, font, fill_color, stroke_color, stroke_width):
        x, y = pos
        if stroke_color is None:
            draw.text((x, y), text, font=font, fill=fill_color)
        else:
            actual_stroke_width = max(2, int(round(stroke_width)))
            draw.text((x, y), text, font=font, fill=fill_color, stroke_width=actual_stroke_width, stroke_fill=stroke_color)

    def generate_thumbnail(self, main_title: str, sub_title: str = "", date_text: str = "", tag_text: str = "",
                          bg_preset_index: int = 0, layout: str = 'left', output_filename: str = None,
                          custom_bg_path: str = None, overlay_opacity: float = 0.5, transform: dict = None) -> str:
        try:
            if bg_preset_index < 0 or bg_preset_index >= len(BACKGROUNDS): bg_preset_index = 0
            bg_config = BACKGROUNDS[bg_preset_index]
            theme = PRESETS.get(bg_config['config']['scheme'], PRESETS['morgan'])
            is_custom = bool(custom_bg_path and os.path.exists(custom_bg_path))
            
            if is_custom:
                img = Image.new('RGBA', (1920, 1080), (0, 21, 41, 255)) 
                screenshot = Image.open(custom_bg_path).convert('RGBA')
                
                # 实现与前端一致的 drawImageCover 逻辑 (带变换)
                target_w, target_h = 1920, 486
                sw_orig, sh_orig = screenshot.size
                r = sw_orig / sh_orig
                cr = target_w / target_h
                
                if r > cr:
                    rh = target_h
                    rw = int(target_h * r)
                else:
                    rw = target_w
                    rh = int(target_w / r)
                
                # 应用变换
                trans = transform or {'x': 0, 'y': 0, 'scale': 1.0}
                scale = trans.get('scale', 1.0)
                offset_x = trans.get('x', 0)
                offset_y = trans.get('y', 0)
                
                sw = int(rw * scale)
                sh = int(rh * scale)
                
                rx = (target_w - rw) // 2
                ry = 0 # 顶部对齐
                
                final_x = rx + (rw - sw) // 2 + int(offset_x)
                final_y = ry + (rh - sh) // 2 + int(offset_y)
                
                screenshot_resized = screenshot.resize((sw, sh), Image.Resampling.LANCZOS)
                
                # 创建一个裁剪层
                paste_layer = Image.new('RGBA', (1920, 1080), (0, 0, 0, 0))
                paste_layer.paste(screenshot_resized, (final_x, final_y))
                
                # 裁剪到顶部区域
                mask = Image.new('L', (1920, 1080), 0)
                mask_draw = ImageDraw.Draw(mask)
                mask_draw.rectangle([0, 0, 1920, 486], fill=255)
                
                img.paste(paste_layer, (0, 0), mask)
            else:
                bg_path_default = os.path.join(self.asset_dir, 'default', bg_config['file'])
                bg_path_root = os.path.join(self.asset_dir, bg_config['file'])
                bg_path = bg_path_default if os.path.exists(bg_path_default) else (bg_path_root if os.path.exists(bg_path_root) else None)
                img = Image.open(bg_path).convert('RGBA') if bg_path else Image.new('RGB', (1920, 1080), theme['bg']).convert('RGBA')
                img = ImageOps.fit(img, (1920, 1080), method=Image.Resampling.LANCZOS)
                blur_amount = bg_config['config'].get('blur', 0)
                if blur_amount > 0: img = img.filter(ImageFilter.GaussianBlur(radius=blur_amount))
                mask_alpha = bg_config['config'].get('mask', 40) / 100.0
                if mask_alpha > 0:
                    mask_layer = Image.new('L', (1920, 1080), 0)
                    mask_draw = ImageDraw.Draw(mask_layer)
                    base_color = (0, 0, 0) if theme['isDark'] else (0, 15, 30)
                    grad_w = int(1920 * 0.7)
                    for x in range(grad_w):
                        alpha = int(255 * mask_alpha * (1 - x / grad_w))
                        line_x = x if layout == 'left' else (1919 - x)
                        mask_draw.line((line_x, 0, line_x, 1080), fill=alpha)
                    img.paste(Image.new('RGBA', (1920, 1080), base_color + (255,)), (0, 0), mask_layer)

            draw = ImageDraw.Draw(img)
            base_x = 120
            # 最终重心上移至 520px
            current_y = 520 if is_custom else 180
            try:
                main_size = 110
                font_main = ImageFont.truetype(self.font_path, main_size)
                font_sub = ImageFont.truetype(self.font_path, 56)
                font_date = ImageFont.truetype(self.font_path, 32)
                font_tag = ImageFont.truetype(self.font_path, 30)
            except: font_main = font_sub = font_date = font_tag = ImageFont.load_default()

            if tag_text and not is_custom:
                l, t, r, b = draw.textbbox((0, 0), tag_text, font=font_tag)
                draw.rounded_rectangle((base_x-24, current_y-12, base_x+(r-l)+24, current_y+(b-t)+12), radius=4, fill='white')
                draw.text((base_x, current_y), tag_text, font=font_tag, fill='#333333')
                current_y += 130

            if main_title:
                main_color = '#FFD700' if is_custom else theme['main']
                stroke_color = None if is_custom else self._parse_rgba(theme['stroke'])
                for line in main_title.split('\n'):
                    if not line.strip(): continue
                    self._draw_halo_text(draw, line, (base_x, current_y), font_main, main_color, stroke_color, main_size * 0.04)
                    current_y += main_size * 1.3
                current_y += 15

            if sub_title:
                sub_color = '#FFFFFF' if is_custom else theme['sub']
                self._draw_halo_text(draw, sub_title, (base_x, current_y), font_sub, sub_color, None if is_custom else self._parse_rgba(theme['stroke']), 56 * 0.04)

            # 落款回归左下角，加装饰线，色调浅灰
            if date_text:
                date_y = 1080 - 100
                f_color = (200, 200, 200, 200) if is_custom else theme['date']
                # 加线
                draw.line((base_x, date_y - 47, base_x + 60, date_y - 47), fill=f_color, width=2)
                self._draw_halo_text(draw, date_text, (base_x, date_y-32), font_date, f_color, None, 0)

            if not output_filename:
                import uuid
                output_filename = f"thumbnail_{uuid.uuid4().hex[:8]}.jpg"
            if not output_filename.lower().endswith('.jpg'): output_filename += '.jpg'
            output_path = os.path.join(self.output_dir, output_filename)
            img.convert('RGB').save(output_path, quality=95)
            logger.info(f"Thumbnail generated: {output_path}")
            return output_path
        except Exception as e:
            logger.error(f"Thumbnail generation failed: {e}", exc_info=True)
            raise e

thumbnail_service = ThumbnailService()
