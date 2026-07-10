import os
import logging
import re
import uuid
from PIL import Image, ImageDraw, ImageFont, ImageFilter, ImageOps
from typing import Dict, Any, Tuple, Optional

from services.frontend_thumbnail_renderer import frontend_thumbnail_renderer

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
        logger.info(f"🎨 [Font Discovery] Selected font: {self.font_path}")

    def _find_font(self):
        env_candidates = [
            os.environ.get('REPORT_THUMBNAIL_FONT_PATH'),
            os.environ.get('THUMBNAIL_FONT_PATH'),
        ]
        candidates = [
            *[path for path in env_candidates if path],
            os.path.join(self.font_base_dir, 'SourceHanSansSC-Heavy.otf'),
            os.path.join(self.font_base_dir, 'SourceHanSansSC-Bold.otf'),
            os.path.join(self.font_base_dir, 'SourceHanSansSC-Regular.otf'),
            os.path.join(self.font_base_dir, 'NotoSansCJKsc-Bold.otf'),
            os.path.join(self.font_base_dir, 'NotoSansCJKsc-Regular.otf'),
            # Modern macOS no longer ships PingFang at this legacy path on all versions.
            '/System/Library/Fonts/Hiragino Sans GB.ttc',
            '/System/Library/Fonts/STHeiti Medium.ttc',
            '/System/Library/Fonts/STHeiti Light.ttc',
            '/System/Library/Fonts/Supplemental/Songti.ttc',
            '/System/Library/Fonts/PingFang.ttc',
            '/System/Library/Fonts/Helvetica.ttc',
            '/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc',
            '/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc',
            '/usr/share/fonts/truetype/source-han-sans/SourceHanSansSC-Heavy.otf',
            '/usr/share/fonts/truetype/wqy/wqy-microhei.ttc',
            'C:\\Windows\\Fonts\\msyhbd.ttc',
            'C:\\Windows\\Fonts\\msyh.ttc',
            'C:\\Windows\\Fonts\\simhei.ttf',
        ]
        for path in candidates:
            if os.path.exists(path):
                logger.info(f"✅ [Font Selection Check] Final font path: {path}")
                return path
        logger.warning("⚠️ [Font Selection Check] No CJK-capable font found.")
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

    def _draw_calendar_icon(self, draw, x, y, size, color):
        # 绘制极简日历图标
        # 外框
        draw.rectangle([x, y + size*0.2, x + size, y + size], outline=color, width=2)
        # 横线
        draw.line([x, y + size*0.45, x + size, y + size*0.45], fill=color, width=2)
        # 两个小竖线 (提手)
        draw.line([x + size*0.25, y, x + size*0.25, y + size*0.3], fill=color, width=2)
        draw.line([x + size*0.75, y, x + size*0.75, y + size*0.3], fill=color, width=2)

    def _font(self, size: int):
        if not self.font_path:
            raise RuntimeError(
                "未找到可渲染中文的封面字体；请安装 Source Han Sans/Noto CJK，"
                "或通过 REPORT_THUMBNAIL_FONT_PATH 指定字体文件。"
            )
        return ImageFont.truetype(self.font_path, size)

    def _text_width(self, draw, text: str, font) -> int:
        left, _top, right, _bottom = draw.textbbox((0, 0), text, font=font)
        return right - left

    def _fit_text_line(self, draw, text: str, font, max_width: int) -> str:
        text = str(text or "").strip()
        if self._text_width(draw, text, font) <= max_width:
            return text
        suffix = "..."
        while text and self._text_width(draw, text + suffix, font) > max_width:
            text = text[:-1].rstrip()
        return (text + suffix) if text else suffix

    def _resize_cover(self, image: Image.Image, target_w: int, target_h: int, *, anchor_y: float = 0.0) -> Image.Image:
        src_w, src_h = image.size
        if src_w <= 0 or src_h <= 0:
            return Image.new("RGBA", (target_w, target_h), (255, 255, 255, 255))
        scale = max(target_w / src_w, target_h / src_h)
        resized_w = max(1, int(src_w * scale))
        resized_h = max(1, int(src_h * scale))
        resized = image.resize((resized_w, resized_h), Image.Resampling.LANCZOS)
        crop_x = max(0, (resized_w - target_w) // 2)
        crop_y = max(0, int((resized_h - target_h) * max(0.0, min(1.0, anchor_y))))
        return resized.crop((crop_x, crop_y, crop_x + target_w, crop_y + target_h))

    def _vertical_title_lines(self, draw, title: str, max_width: int, max_lines: int):
        compact = "".join(str(title or "").split())
        base_size = 66 if len(compact) <= 18 else 60 if len(compact) <= 26 else 56
        for size in range(base_size, 47, -4):
            font = self._font(size)
            lines = self._wrap_vertical_text(draw, title, font, max_width, max_lines)
            line_gap = int(size * 1.18)
            if len(lines) * line_gap <= 310:
                return lines, font, size, line_gap
        font = self._font(48)
        return self._wrap_vertical_text(draw, title, font, max_width, max_lines), font, 48, 58

    def _draw_vertical_title_line(self, draw, x: int, y: int, line: str, font, *, accent=(226, 198, 82, 255), text=(245, 248, 252, 255)):
        value = str(line or "")
        colon_positions = [pos for pos in (value.find(":"), value.find("：")) if pos >= 0]
        if colon_positions and min(colon_positions) <= 5:
            split_at = min(colon_positions) + 1
            prefix = value[:split_at]
            rest = value[split_at:].lstrip()
            draw.text((x, y), prefix, font=font, fill=accent)
            offset = self._text_width(draw, prefix, font) + 14
            draw.text((x + offset, y), rest, font=font, fill=text)
            return
        draw.text((x, y), value, font=font, fill=text)

    def _wrap_vertical_text(self, draw, text: str, font, max_width: int, max_lines: int):
        raw = str(text or "").strip()
        if not raw or max_lines <= 0:
            return []

        lines = []
        for paragraph in raw.splitlines():
            paragraph = paragraph.strip()
            if not paragraph:
                continue
            tokens = re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9.]+|[^\u4e00-\u9fff]", paragraph)
            current = ""
            for token in tokens:
                candidate = current + token
                if current and self._text_width(draw, candidate, font) > max_width:
                    lines.append(current.strip())
                    current = token
                else:
                    current = candidate
            if current.strip():
                lines.append(current.strip())

        if len(lines) > max_lines:
            trimmed = lines[:max_lines]
            trimmed[-1] = self._fit_text_line(draw, trimmed[-1], font, max_width)
            return trimmed
        return lines

    def generate_vertical_thumbnail(self, main_title: str, sub_title: str = "", date_text: str = "",
                                    screenshot_path: str = None, output_filename: str = None) -> str:
        """Generate the 9:16 vertical background used by vertical video synthesis."""
        try:
            width, height = 1080, 1920
            preview_h = 520
            content_top = preview_h + 132
            left_padding = 86
            right_padding = 86
            max_text_width = width - left_padding - right_padding
            navy_top = (7, 23, 39)
            navy_bottom = (3, 10, 22)
            gold = (226, 198, 82, 255)
            white = (246, 248, 252, 255)
            muted = (214, 222, 232, 210)

            img = Image.new("RGBA", (width, height), (*navy_bottom, 255))
            draw = ImageDraw.Draw(img)

            for y in range(height):
                ratio = y / max(1, height - 1)
                color = tuple(int(navy_top[i] * (1 - ratio) + navy_bottom[i] * ratio) for i in range(3))
                draw.line((0, y, width, y), fill=(*color, 255))

            if screenshot_path and os.path.exists(screenshot_path):
                screenshot = Image.open(screenshot_path).convert("RGBA")
                preview = self._resize_cover(screenshot, width, preview_h, anchor_y=0.0)
                img.paste(preview, (0, 0), preview)
            else:
                draw.rectangle([0, 0, width, preview_h], fill=(245, 247, 250, 255))
                placeholder_font = self._font(30)
                placeholder = "等待上传研报截图..."
                text_w = self._text_width(draw, placeholder, placeholder_font)
                draw.text(((width - text_w) / 2, preview_h / 2 - 20), placeholder, font=placeholder_font, fill=(148, 163, 184, 255))

            overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            overlay_draw = ImageDraw.Draw(overlay)
            fade_start = preview_h - 180
            fade_end = preview_h + 112
            for y in range(fade_start, fade_end):
                ratio = (y - fade_start) / max(1, fade_end - fade_start)
                alpha = int(238 * ratio)
                overlay_draw.line((0, y, width, y), fill=(*navy_top, alpha))
            img = Image.alpha_composite(img, overlay)

            focus = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            focus_draw = ImageDraw.Draw(focus)
            focus_draw.ellipse(
                [left_padding - 360, content_top - 170, left_padding + 650, content_top + 520],
                fill=(226, 198, 82, 18),
            )
            focus_draw.ellipse(
                [width - 430, height - 560, width + 260, height + 180],
                fill=(20, 74, 112, 22),
            )
            focus = focus.filter(ImageFilter.GaussianBlur(radius=96))
            img = Image.alpha_composite(img, focus)
            draw = ImageDraw.Draw(img)

            draw.line((left_padding, content_top - 52, left_padding + 136, content_top - 52), fill=gold, width=5)
            draw.line((0, preview_h, width, preview_h), fill=(226, 198, 82, 42), width=2)

            title_lines, main_font, main_font_size, line_gap = self._vertical_title_lines(
                draw,
                main_title,
                max_text_width,
                4,
            )
            current_y = content_top
            for line in title_lines:
                self._draw_vertical_title_line(draw, left_padding, current_y, line, main_font, accent=gold, text=white)
                current_y += line_gap

            sub_font = self._font(34)
            date_font = self._font(28)
            sub_lines = self._wrap_vertical_text(draw, sub_title, sub_font, max_text_width, 2)
            if sub_lines:
                current_y += 24
                for line in sub_lines[:2]:
                    draw.text((left_padding, current_y), line, font=sub_font, fill=muted)
                    current_y += int(34 * 1.28)

            if date_text:
                footer_y = min(current_y + 46, 1120)
                draw.rectangle([left_padding, footer_y - 25, left_padding + 92, footer_y - 22], fill=(255, 255, 255, 145))
                draw.text((left_padding, footer_y - 6), str(date_text), font=date_font, fill=(235, 239, 245, 178))

            lower_top = min(max((footer_y if date_text else current_y) + 84, 1090), 1240)
            caption_top = height - 430
            caption_bottom = height - 156
            stage_overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            stage_draw = ImageDraw.Draw(stage_overlay)
            for y in range(lower_top, height):
                ratio = (y - lower_top) / max(1, height - lower_top)
                alpha = int(10 + 58 * ratio)
                stage_draw.line((0, y, width, y), fill=(1, 6, 14, alpha))

            rail_top = lower_top + 18
            rail_bottom = caption_top - 94
            if rail_bottom > rail_top:
                stage_draw.line((left_padding, rail_top, left_padding, rail_bottom), fill=(226, 198, 82, 34), width=2)
                stage_draw.ellipse(
                    [left_padding - 5, rail_top - 5, left_padding + 5, rail_top + 5],
                    fill=(226, 198, 82, 86),
                )
                for index, y in enumerate((rail_top, rail_top + 96, rail_top + 192)):
                    if y < rail_bottom - 20:
                        alpha = 22 - index * 5
                        stage_draw.line((left_padding + 26, y, left_padding + 150, y), fill=(148, 163, 184, alpha), width=1)

            stage_draw.line((left_padding, caption_top, width - right_padding, caption_top), fill=(226, 198, 82, 42), width=2)
            stage_draw.line((left_padding, caption_bottom, width - right_padding, caption_bottom), fill=(148, 163, 184, 24), width=1)
            stage_draw.line((left_padding, caption_top, left_padding, caption_top + 74), fill=(226, 198, 82, 34), width=2)
            stage_draw.line((width - right_padding, caption_top, width - right_padding, caption_top + 74), fill=(226, 198, 82, 18), width=2)
            stage_draw.line((left_padding, caption_bottom - 64, left_padding, caption_bottom), fill=(148, 163, 184, 22), width=1)
            stage_draw.line((width - right_padding, caption_bottom - 64, width - right_padding, caption_bottom), fill=(148, 163, 184, 16), width=1)
            for y in (caption_top + 92, caption_top + 174):
                stage_draw.line((left_padding + 34, y, width - right_padding - 34, y), fill=(148, 163, 184, 10), width=1)
            img = Image.alpha_composite(img, stage_overlay)

            if not output_filename:
                output_filename = f"vertical_thumbnail_{uuid.uuid4().hex[:8]}.jpg"
            if not output_filename.lower().endswith((".jpg", ".jpeg")):
                output_filename += ".jpg"
            output_path = os.path.join(self.output_dir, output_filename)
            img.convert("RGB").save(output_path, quality=95)
            logger.info(f"Vertical thumbnail generated: {output_path}")
            return output_path
        except Exception as e:
            logger.error(f"Vertical thumbnail generation failed: {e}", exc_info=True)
            raise e

    def generate_workbench_vertical_thumbnail(
        self,
        main_title: str,
        sub_title: str = "",
        *,
        screenshot_path: str = None,
        output_filename: str = None,
        institution: str = "深度研报",
        emotion: str = "bull",
        footer_text: str = "听见资本",
        image_transform: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Generate a 9:16 background with the thumbnail workbench vertical layout."""
        if not output_filename:
            output_filename = f"workbench_vertical_{uuid.uuid4().hex[:8]}.jpg"
        if not output_filename.lower().endswith((".jpg", ".jpeg")):
            output_filename += ".jpg"
        output_path = os.path.join(self.output_dir, output_filename)
        transform = image_transform if isinstance(image_transform, dict) else {}
        def _int_transform(name: str, default: int) -> int:
            try:
                return int(transform.get(name) or default)
            except (TypeError, ValueError):
                return default

        rendered = frontend_thumbnail_renderer.render_workbench_thumbnail(
            output_path=output_path,
            format_name="vertical",
            main_title=main_title,
            sub_title=sub_title,
            footer_text=footer_text,
            institution=institution or "深度研报",
            emotion=emotion if emotion in {"pro", "macro", "bull", "bear"} else "bull",
            image_path=screenshot_path,
            image_offset_x=transform.get("x") or 0,
            image_offset_y=transform.get("y") or 0,
            image_scale=transform.get("scale") or 1,
            image_anchor=transform.get("anchor") or transform.get("image_anchor") or "top-left",
            title_max_lines=_int_transform("title_max_lines", 2),
            title_min_size=_int_transform("title_min_size", 74),
            subtitle_max_lines=_int_transform("subtitle_max_lines", 1),
            subtitle_min_size=_int_transform("subtitle_min_size", 42),
            show_guides=False,
            expected_size=(1080, 1920),
        )
        logger.info("Workbench vertical thumbnail generated: %s", rendered)
        return rendered

    def generate_workbench_vertical_cover(
        self,
        main_title: str,
        sub_title: str = "",
        *,
        screenshot_path: str = None,
        output_filename: str = None,
        institution: str = "深度研报",
        emotion: str = "bull",
        footer_text: str = "听见资本",
        image_transform: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Generate the 3:4 WeChat Channels style cover for scheme2 vertical video."""
        if not output_filename:
            output_filename = f"workbench_wx_cover_{uuid.uuid4().hex[:8]}.jpg"
        if not output_filename.lower().endswith((".jpg", ".jpeg")):
            output_filename += ".jpg"
        output_path = os.path.join(self.output_dir, output_filename)
        transform = image_transform if isinstance(image_transform, dict) else {}

        def _int_transform(name: str, default: int) -> int:
            try:
                return int(transform.get(name) or default)
            except (TypeError, ValueError):
                return default

        rendered = frontend_thumbnail_renderer.render_workbench_thumbnail(
            output_path=output_path,
            format_name="wx_cover",
            main_title=main_title,
            sub_title=sub_title,
            footer_text=footer_text,
            institution=institution or "深度研报",
            emotion=emotion if emotion in {"pro", "macro", "bull", "bear"} else "bull",
            image_path=screenshot_path,
            image_offset_x=transform.get("x") or 0,
            image_offset_y=transform.get("y") or 0,
            image_scale=transform.get("scale") or 1,
            image_anchor=transform.get("anchor") or transform.get("image_anchor") or "top-left",
            title_max_lines=_int_transform("title_max_lines", 2),
            title_min_size=_int_transform("title_min_size", 72),
            subtitle_max_lines=_int_transform("subtitle_max_lines", 1),
            subtitle_min_size=_int_transform("subtitle_min_size", 40),
            show_guides=False,
            expected_size=(1080, 1440),
        )
        logger.info("Workbench vertical cover generated: %s", rendered)
        return rendered

    def generate_workbench_podcast_cover(
        self,
        main_title: str,
        sub_title: str = "",
        *,
        screenshot_path: str = None,
        output_filename: str = None,
        institution: str = "深度研报",
        emotion: str = "bull",
        footer_text: str = "听见资本",
        image_transform: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Generate the 1:1 podcast cover for scheme2/Bilibili-safe publishing."""
        if not output_filename:
            output_filename = f"workbench_podcast_cover_{uuid.uuid4().hex[:8]}.jpg"
        if not output_filename.lower().endswith((".jpg", ".jpeg")):
            output_filename += ".jpg"
        output_path = os.path.join(self.output_dir, output_filename)
        transform = image_transform if isinstance(image_transform, dict) else {}

        def _int_transform(name: str, default: int) -> int:
            try:
                return int(transform.get(name) or default)
            except (TypeError, ValueError):
                return default

        rendered = frontend_thumbnail_renderer.render_workbench_thumbnail(
            output_path=output_path,
            format_name="podcast",
            main_title=main_title,
            sub_title=sub_title,
            footer_text=footer_text,
            institution=institution or "深度研报",
            emotion=emotion if emotion in {"pro", "macro", "bull", "bear"} else "bull",
            image_path=screenshot_path,
            image_offset_x=transform.get("x") or 0,
            image_offset_y=transform.get("y") or 0,
            image_scale=transform.get("scale") or 1,
            image_anchor=transform.get("anchor") or transform.get("image_anchor") or "top-left",
            title_max_lines=_int_transform("title_max_lines", 2),
            title_min_size=_int_transform("title_min_size", 76),
            subtitle_max_lines=_int_transform("subtitle_max_lines", 1),
            subtitle_min_size=_int_transform("subtitle_min_size", 42),
            show_guides=False,
            expected_size=(1400, 1400),
        )
        logger.info("Workbench podcast cover generated: %s", rendered)
        return rendered

    def generate_workbench_bilibili_cover(
        self,
        main_title: str,
        sub_title: str = "",
        *,
        screenshot_path: str = None,
        output_filename: str = None,
        institution: str = "深度研报",
        emotion: str = "bull",
        footer_text: str = "听见资本",
        image_transform: Optional[Dict[str, Any]] = None,
        show_footer: bool = False,
    ) -> str:
        """Generate the Bilibili cover with the WeChat Channels cover style."""
        if not output_filename:
            output_filename = f"workbench_bilibili_cover_{uuid.uuid4().hex[:8]}.jpg"
        if not output_filename.lower().endswith((".jpg", ".jpeg")):
            output_filename += ".jpg"
        output_path = os.path.join(self.output_dir, output_filename)
        transform = image_transform if isinstance(image_transform, dict) else {}

        def _int_transform(name: str, default: int) -> int:
            try:
                return int(transform.get(name) or default)
            except (TypeError, ValueError):
                return default

        rendered = frontend_thumbnail_renderer.render_workbench_thumbnail(
            output_path=output_path,
            format_name="bilibili_wx_cover",
            main_title=main_title,
            sub_title=sub_title,
            footer_text=footer_text,
            institution=institution or "深度研报",
            emotion=emotion if emotion in {"pro", "macro", "bull", "bear"} else "bull",
            image_path=screenshot_path,
            image_offset_x=transform.get("x") or 0,
            image_offset_y=transform.get("y") or 0,
            image_scale=transform.get("scale") or 1,
            image_anchor=transform.get("anchor") or transform.get("image_anchor") or "top-left",
            title_max_lines=_int_transform("title_max_lines", 2),
            title_min_size=_int_transform("title_min_size", 42),
            subtitle_max_lines=_int_transform("subtitle_max_lines", 1),
            subtitle_min_size=_int_transform("subtitle_min_size", 32),
            show_guides=False,
            show_footer=show_footer,
            expected_size=(1146, 717),
        )
        logger.info("Workbench Bilibili cover generated: %s", rendered)
        return rendered

    def generate_thumbnail(self, main_title: str, sub_title: str = "", date_text: str = "", tag_text: str = "",
                          bg_preset_index: int = 0, layout: str = 'left', output_filename: str = None,
                          custom_bg_path: str = None, overlay_opacity: float = 0.5, transform: dict = None,
                          mode: str = 'preset') -> str:
        try:
            if bg_preset_index < 0 or bg_preset_index >= len(BACKGROUNDS): bg_preset_index = 0
            bg_config = BACKGROUNDS[bg_preset_index]
            theme = PRESETS.get(bg_config['config']['scheme'], PRESETS['morgan'])
            
            is_screenshot = (mode == 'screenshot')
            is_podcast = (mode == 'podcast')
            is_custom_bg = bool(custom_bg_path and os.path.exists(custom_bg_path))
            
            # 判断是否应用特定视觉标识 (VI)
            is_screenshot_style = is_screenshot or is_podcast

            renderer_mode = os.getenv("REPORT_COVER_RENDERER", "frontend").strip().lower()
            if is_screenshot and renderer_mode not in {"pil", "legacy", "off", "0", "false"}:
                try:
                    frontend_output_filename = output_filename or f"thumbnail_{uuid.uuid4().hex[:8]}.jpg"
                    if not frontend_output_filename.lower().endswith('.jpg'):
                        frontend_output_filename += '.jpg'
                    frontend_output_path = os.path.join(self.output_dir, frontend_output_filename)
                    image_path = custom_bg_path if custom_bg_path and os.path.exists(custom_bg_path) else None
                    rendered = frontend_thumbnail_renderer.render_landscape_cover(
                        output_path=frontend_output_path,
                        main_title=main_title or "",
                        sub_title=sub_title or "",
                        date_text=date_text or "",
                        tag_text=tag_text or "",
                        mode=mode,
                        custom_bg_path=image_path,
                        transform=transform or {'x': 0, 'y': 0, 'scale': 1},
                        quality=0.95,
                    )
                    logger.info(f"Thumbnail generated by frontend renderer: {rendered}")
                    return rendered
                except Exception as exc:
                    if renderer_mode in {"frontend-only", "browser-only", "strict"}:
                        raise
                    logger.warning(
                        "Frontend thumbnail renderer failed; falling back to PIL renderer: %s",
                        exc,
                        exc_info=True,
                    )

            if is_screenshot_style and is_custom_bg:
                # 实现与前端一致的 drawImageCover 逻辑 (带变换)
                img = Image.new('RGBA', (1920, 1080), (0, 21, 41, 255)) 
                screenshot = Image.open(custom_bg_path).convert('RGBA')
                
                target_w, target_h = 1920, (486 if is_screenshot else 1080)
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
                scale = float(trans.get('scale', 1.0))
                offset_x = float(trans.get('x', 0))
                offset_y = float(trans.get('y', 0))
                
                sw = int(rw * scale)
                sh = int(rh * scale)
                
                rx = (target_w - rw) // 2
                ry = 0 # 顶部对齐
                
                final_x = rx + (rw - sw) // 2 + int(offset_x)
                final_y = ry + (rh - sh) // 2 + int(offset_y)
                
                screenshot_resized = screenshot.resize((sw, sh), Image.Resampling.LANCZOS)
                
                if is_screenshot:
                    # 截图模式：裁剪到顶部 45% 区域，底部为深蓝色
                    paste_layer = Image.new('RGBA', (1920, 1080), (0, 0, 0, 0))
                    paste_layer.paste(screenshot_resized, (final_x, final_y))
                    mask = Image.new('L', (1920, 1080), 0)
                    mask_draw = ImageDraw.Draw(mask)
                    mask_draw.rectangle([0, 0, 1920, 486], fill=255)
                    img.paste(paste_layer, (0, 0), mask)
                    
                    # 底部背景深蓝色
                    draw_bg = ImageDraw.Draw(img)
                    draw_bg.rectangle([0, 486, 1920, 1080], fill=(0, 21, 41, 255))
                else:
                    # 播客模式：全屏图片 + 遮罩
                    img.paste(screenshot_resized, (final_x, final_y))
                    # 播客全屏遮罩：左侧深色渐变 (匹配前端更深的渐变)
                    mask_layer = Image.new('L', (1920, 1080), 0)
                    mask_draw = ImageDraw.Draw(mask_layer)
                    grad_w = int(1920 * 0.8)
                    for x in range(grad_w):
                        ratio = x / grad_w
                        if ratio < 0.4:
                            alpha_ratio = 0.92 - (ratio / 0.4) * 0.32
                        else:
                            alpha_ratio = 0.6 - ((ratio - 0.4) / 0.6) * 0.6
                        alpha = int(255 * max(0, alpha_ratio))
                        mask_draw.line((x, 0, x, 1080), fill=alpha)
                    img.paste(Image.new('RGBA', (1920, 1080), (10, 10, 10, 255)), (0, 0), mask_layer)
            else:
                # 预设模式或未上传截图
                if is_podcast:
                    img = Image.new('RGBA', (1920, 1080), (17, 17, 17, 255)) # #111111
                else:
                    bg_path_default = os.path.join(self.asset_dir, 'default', bg_config['file'])
                    bg_path_root = os.path.join(self.asset_dir, bg_config['file'])
                    bg_path = bg_path_default if os.path.exists(bg_path_default) else (bg_path_root if os.path.exists(bg_path_root) else None)
                    
                    if bg_path:
                        img = Image.open(bg_path).convert('RGBA')
                    else:
                        img = Image.new('RGB', (1920, 1080), theme['bg']).convert('RGBA')
                    
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
            # 垂直重心位置：截图模式 520, 播客模式 420, 预设模式 180
            current_y = 520 if is_screenshot else (420 if is_podcast else 180)
            
            try:
                if not self.font_path:
                    raise RuntimeError(
                        "未找到可渲染中文的封面字体；请安装 Source Han Sans/Noto CJK，"
                        "或通过 REPORT_THUMBNAIL_FONT_PATH 指定字体文件。"
                    )
                main_size = 110
                font_main = ImageFont.truetype(self.font_path, main_size)
                font_sub = ImageFont.truetype(self.font_path, 56)
                font_date = ImageFont.truetype(self.font_path, 32)
                font_tag = ImageFont.truetype(self.font_path, 30)
            except RuntimeError:
                raise
            except Exception as exc:
                raise RuntimeError(f"封面字体加载失败: {self.font_path}: {exc}") from exc

            # --- 播客模式新增：顶部身份标签栏 (包裹文字的小标签) ---
            if is_podcast:
                tag_top = 320
                bar_h = 100
                tag_padding = 48
                try:
                    font_id_tag = ImageFont.truetype(self.font_path, 62)
                except:
                    font_id_tag = font_tag
                
                tag_content = "内资机构 · 闭门会"
                l, t, r, b = draw.textbbox((0, 0), tag_content, font=font_id_tag)
                text_w = r - l
                tag_w = text_w + tag_padding * 2
                
                # 绘制金色实心背景小标签 (与主标题对齐)
                draw.rounded_rectangle([base_x, tag_top, base_x + tag_w, tag_top + bar_h], radius=10, fill=(255, 215, 0))
                
                # 计算文字居中位置
                text_x = base_x + (tag_w - text_w) // 2 - l
                text_y = tag_top + (bar_h - (b - t)) // 2 - t + 5
                draw.text((text_x, text_y), tag_content, font=font_id_tag, fill=(0, 0, 0))


            if tag_text and not is_screenshot_style:
                l, t, r, b = draw.textbbox((0, 0), tag_text, font=font_tag)
                draw.rounded_rectangle((base_x-24, current_y-12, base_x+(r-l)+24, current_y+(b-t)+12), radius=4, fill='white')
                draw.text((base_x, current_y), tag_text, font=font_tag, fill='#333333')
                current_y += 130

            if main_title:
                main_color = '#FFD700' if is_screenshot_style else theme['main']
                stroke_color = None if is_screenshot_style else self._parse_rgba(theme['stroke'])
                for line in main_title.split('\n'):
                    if not line.strip(): continue
                    self._draw_halo_text(draw, line, (base_x, current_y), font_main, main_color, stroke_color, main_size * 0.04)
                    current_y += int(main_size * 1.3)
                current_y += 15

            if sub_title:
                sub_color = '#FFFFFF' if is_screenshot_style else theme['sub']
                for line in str(sub_title).split('\n'):
                    if not line.strip():
                        continue
                    self._draw_halo_text(
                        draw,
                        line,
                        (base_x, current_y),
                        font_sub,
                        sub_color,
                        None if is_screenshot_style else self._parse_rgba(theme['stroke']),
                        56 * 0.04,
                    )
                    current_y += int(56 * 1.25)

            # 落款回归左下角
            if date_text:
                date_y = 1080 - 100
                f_color = (255, 255, 255, 204) if is_screenshot_style else theme['date']
                
                # Podcast 模式下对齐主标题，增加日历图标
                if (is_podcast):
                    icon_size = 28
                    self._draw_calendar_icon(draw, base_x, date_y - 33, icon_size, f_color)
                    self._draw_halo_text(draw, date_text, (base_x + icon_size + 15, date_y - 32), font_date, f_color, None, 0)
                else:
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
