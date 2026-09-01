import json
import math
import os
import re
import shlex
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace

import fitz  # type: ignore
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from services.report_video_service import PROJECT_ROOT, rel_path, task_dir


FONT_CANDIDATES = [
    "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
]

DEFAULT_SOURCE_CARD_DURATION_SEC = 5.0
IMPORTANT_SOURCE_CARD_DURATION_SEC = 6.0
MAX_XFADE_FRAME_ITEMS = int(os.environ.get("REPORT_VIDEO_MAX_XFADE_FRAME_ITEMS", "40"))
MAX_XFADE_DURATION_SEC = float(os.environ.get("REPORT_VIDEO_MAX_XFADE_DURATION_SEC", "900"))
REPORT_VIDEO_RENDER_PREVIEW_VERSION = "pillow_ffmpeg_preview_v2_stitch_portrait"
_PDF_FILENAME_CACHE = {}

CANVAS_BG = "#07111f"
CANVAS_TOP_BG = "#0a1627"
CANVAS_SUBTITLE_BG = "#050a14"
PANEL_DARK = "#0a1424"
PANEL_DARK_ALT = "#0e1a2d"
PANEL_LIGHT = "#faf7ec"
PANEL_LIGHT_BORDER = "#e8dcc2"
FINANCE_GOLD = "#facc15"
FINANCE_GOLD_SOFT = "#fef3c7"
FINANCE_GOLD_DARK = "#78350f"
FINANCE_BLUE = "#60a5fa"
PORTRAIT_PRIMARY = "#ffb4aa"
PORTRAIT_PRIMARY_SOFT = "#ffdad5"
PORTRAIT_SURFACE = "#121414"
PORTRAIT_GLASS = (18, 24, 32, 214)
PORTRAIT_GLASS_BORDER = (255, 255, 255, 44)

SUBTITLE_HIGHLIGHT_TERMS = [
    "AI算力",
    "电力基础设施",
    "数据中心",
    "公用事业",
    "资本支出",
    "储能系统",
    "储能需求",
    "储能",
    "电气设备",
    "宁德时代",
    "锂电池",
    "Quanta Services",
    "Quanta",
    "Eaton",
    "Hubbell",
    "伯恩斯坦",
]

SUBTITLE_HIGHLIGHT_RE = re.compile(
    "("
    + "|".join(re.escape(term) for term in sorted(SUBTITLE_HIGHLIGHT_TERMS, key=len, reverse=True))
    + r"|\d+(?:\.\d+)?(?:%|％|GW|GWh|TWh|MWh|KWh|港元|美元|元|倍|年|月|日|份|家)?)"
)


def media_duration(path):
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        return float(result.stdout.strip())
    except (subprocess.SubprocessError, ValueError):
        return None


def audio_stream_profile(path):
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "a:0",
                "-show_entries",
                "stream=sample_rate,channels,channel_layout",
                "-of",
                "json",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        payload = json.loads(result.stdout or "{}")
        stream = ((payload.get("streams") or [None])[0]) or {}
        sample_rate = int(stream.get("sample_rate") or 44100)
        channels = max(int(stream.get("channels") or 2), 1)
        channel_layout = (stream.get("channel_layout") or "").strip()
        if not channel_layout:
            channel_layout = "mono" if channels == 1 else "stereo"
        return {
            "sample_rate": sample_rate,
            "channels": channels,
            "channel_layout": channel_layout,
        }
    except (subprocess.SubprocessError, ValueError, json.JSONDecodeError, TypeError):
        return {
            "sample_rate": 44100,
            "channels": 2,
            "channel_layout": "stereo",
        }


def safe_filename(value):
    name = re.sub(r'[\\/*?:"<>|]', "", value or "")
    name = re.sub(r"\s+", "_", name).strip("._")
    return name or uuid.uuid4().hex[:8]


def load_font(size, bold=False):
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size=size)
    return ImageFont.load_default()


_WRAP_PROTECTED_TERMS = [
    "Quanta Services",
    "AI算力",
    "数据中心",
    "电力基础设施",
    "公用事业",
    "资本支出",
    "储能系统",
    "储能需求",
    "储能扩张",
    "宁德时代",
    "锂电池",
    "伯恩斯坦",
    "电气设备制造商",
    "全面重估",
    "复合效应",
    "输电投资",
    "配电",
    "目标价",
]
_WRAP_TOKEN_RE = re.compile(
    "("
    + "|".join(re.escape(item) for item in sorted(_WRAP_PROTECTED_TERMS, key=len, reverse=True))
    + r"|[A-Za-z][A-Za-z0-9.+/%％-]*(?:\s+[A-Za-z][A-Za-z0-9.+/%％-]*)*"
    + r"|\d+(?:\.\d+)?(?:[%％A-Za-z万亿兆港美元年月日点个份家条股倍TWhKWhMWhGWh]*)?"
    + r"|.)"
)


_DISPLAY_TEXT_REPLACEMENTS = (
    ("博恩斯坦", "伯恩斯坦"),
    ("言报", "研报"),
    ("基础事实", "基础设施"),
    ("基础试试", "基础设施"),
    ("电器设备", "电气设备"),
    ("电 器", "电气"),
    ("Gwanta", "Quanta"),
    ("Ethan", "Eaton"),
    ("Hubble", "Hubbell"),
    ("书店投资", "输电投资"),
    ("书店增长", "输电增长"),
    ("书店", "输电"),
    ("复核效应", "复合效应"),
    ("铃电池", "锂电池"),
    ("理电池", "锂电池"),
    ("TW时", "TWh"),
)


def _display_text(value):
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    text = text.replace(",", "，")
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[，。！？；：、,.!?;:%％])", "", text)
    text = re.sub(r"(?<=[，。！？；：、,.!?;:])\s+(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"(?<=[\u4e00-\u9fff])[,](?=[\u4e00-\u9fffA-Za-z0-9])", "，", text)
    text = re.sub(r"(?<=[A-Za-z0-9%％])[,](?=[\u4e00-\u9fff])", "，", text)
    text = re.sub(r"(?<=\d)\s+(?=[%％])", "", text)
    text = re.sub(r"(?<=[%％])\s+(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"(?<=\d)\.\s+(?=\d)", ".", text)
    text = re.sub(
        r"(?<=\d)\s+(?=\d+(?:GW|GWh|TWh|MWh|KWh|%|％|港元|美元|元|万|亿|兆|个|份|家|条|股|倍|年|月|日))",
        "",
        text,
    )
    for source, target in _DISPLAY_TEXT_REPLACEMENTS:
        text = text.replace(source, target)
    return text


def _wrap_tokens(text):
    return [match.group(0) for match in _WRAP_TOKEN_RE.finditer(text or "")]


def _append_wrapped_line(lines, line):
    line = (line or "").strip()
    if line:
        lines.append(line)


def wrap_text(draw, text, font, max_width, max_lines=None):
    text = _display_text(text)
    if not text:
        return []

    lines = []
    current = ""
    for token in _wrap_tokens(text):
        if token.isspace() and not current:
            continue
        candidate = current + token
        if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
            current = candidate
            continue

        if current:
            _append_wrapped_line(lines, current)
            current = token.lstrip()
        elif draw.textbbox((0, 0), token, font=font)[2] <= max_width:
            current = token.lstrip()
        else:
            # Extremely long tokens still need to render; split them character by character.
            for char in token:
                candidate = current + char
                if draw.textbbox((0, 0), candidate, font=font)[2] <= max_width:
                    current = candidate
                else:
                    _append_wrapped_line(lines, current)
                    current = char
                if max_lines and len(lines) >= max_lines:
                    break
        if max_lines and len(lines) >= max_lines:
            break
    if current and (not max_lines or len(lines) < max_lines):
        _append_wrapped_line(lines, current)
    if max_lines and len(lines) == max_lines and len("".join(lines)) < len(text):
        lines[-1] = lines[-1].rstrip("。,.， ") + "..."
    return lines


def draw_rounded_rect(draw, xy, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def draw_centered_text(draw, center_x, top_y, lines, font, fill, line_gap):
    for idx, line in enumerate(lines or []):
        bbox = draw.textbbox((0, 0), line, font=font)
        line_width = bbox[2] - bbox[0]
        draw.text((center_x - line_width / 2, top_y + idx * line_gap), line, fill=fill, font=font)


def draw_tag(draw, x, y, text, font, fill, text_fill, padding_x=16, padding_y=8, radius=16, outline=None, outline_width=1):
    text = _display_text(text)
    bbox = draw.textbbox((0, 0), text, font=font)
    tag_w = bbox[2] - bbox[0] + padding_x * 2
    tag_h = bbox[3] - bbox[1] + padding_y * 2
    draw_rounded_rect(draw, (x, y, x + tag_w, y + tag_h), radius, fill, outline, outline_width)
    draw.text((x + padding_x, y + padding_y - 1), text, fill=text_fill, font=font)
    return x + tag_w, y + tag_h


def draw_grid_background(draw, width, height, step=54, color="#e6edf7"):
    for x in range(0, width, step):
        draw.line((x, 0, x, height), fill=color, width=1)
    for y in range(0, height, step):
        draw.line((0, y, width, y), fill=color, width=1)


def draw_panel_shadow(draw, xy, radius=28, offset=8, fill=(15, 23, 42, 32)):
    x1, y1, x2, y2 = xy
    draw_rounded_rect(draw, (x1 + offset, y1 + offset, x2 + offset, y2 + offset), radius, fill)


def _draw_finance_backdrop(draw, width, height, subtitle_top=None, top_h=0, grid_region=None):
    subtitle_top = int(subtitle_top if subtitle_top is not None else height)
    draw.rectangle((0, 0, width, height), fill=CANVAS_BG)
    draw.rectangle((0, 0, width, max(top_h, 0)), fill=CANVAS_TOP_BG)
    draw.rectangle((0, subtitle_top, width, height), fill=CANVAS_SUBTITLE_BG)

    # Kept intentionally quiet: this reads as a financial studio backdrop, not a UI grid.
    draw.rectangle((0, top_h, width, subtitle_top), fill="#050d1b")
    draw.ellipse((-260, 122, 420, 732), fill="#0e2442")
    draw.ellipse((width - 420, -180, width + 220, 480), fill="#17160b")
    draw.ellipse((width - 360, subtitle_top - 220, width + 160, subtitle_top + 240), fill="#10213c")

    if grid_region:
        _draw_region_grid(draw, grid_region, step=132, color="#142640")

    spark_points = [
        (width - 610, top_h + 84),
        (width - 548, top_h + 72),
        (width - 490, top_h + 112),
        (width - 432, top_h + 94),
        (width - 372, top_h + 142),
        (width - 306, top_h + 118),
        (width - 248, top_h + 156),
    ]
    for start, end in zip(spark_points, spark_points[1:]):
        draw.line((start[0], start[1], end[0], end[1]), fill="#5d4a14", width=2)
    for x, y in spark_points[1::2]:
        draw.ellipse((x - 3, y - 3, x + 3, y + 3), fill="#a78922")


def _subtitle_rich_segments(line):
    segments = []
    cursor = 0
    for match in SUBTITLE_HIGHLIGHT_RE.finditer(line or ""):
        if match.start() > cursor:
            segments.append((line[cursor:match.start()], False))
        segments.append((match.group(0), True))
        cursor = match.end()
    if cursor < len(line or ""):
        segments.append((line[cursor:], False))
    return [(text, highlight) for text, highlight in segments if text]


def _draw_rich_centered_text(draw, line, y, font, width, base_fill="#f8fafc", accent_fill=FINANCE_GOLD):
    segments = _subtitle_rich_segments(line)
    if not segments:
        return
    total_width = sum(draw.textbbox((0, 0), text, font=font)[2] for text, _ in segments)
    x = (width - total_width) / 2
    for text, highlight in segments:
        fill = accent_fill if highlight else base_fill
        bbox = draw.textbbox((0, 0), text, font=font)
        draw.text((x + 2, y + 2), text, font=font, fill=(0, 0, 0, 160))
        draw.text(
            (x, y),
            text,
            font=font,
            fill=fill,
            stroke_width=1,
            stroke_fill="#020617",
        )
        x += bbox[2] - bbox[0]


def node_map(mindmap):
    nodes = (mindmap.nodes if mindmap else []) or []
    return {str(node.get("id")): node for node in nodes if node.get("id")}


def _trim_text(value, limit):
    text = _display_text(value)
    if len(text) <= limit:
        return text
    return text[: max(limit - 1, 1)].rstrip("，,。；;：: ") + "..."


def _segment_focus_node_id(segment):
    payload = getattr(segment, "raw_payload", None) or {}
    return (
        getattr(segment, "focus_node_id", None)
        or getattr(segment, "mindmap_node_id", None)
        or payload.get("mindmap_node_id")
        or payload.get("focus_node_id")
    )


def _parent_node_for(nodes, target_id):
    if not target_id:
        return None
    for node in nodes.values():
        if str(target_id) in [str(child_id) for child_id in (node.get("children") or [])]:
            return node
    return None


def _node_children(nodes, node):
    if not node:
        return []
    return [nodes[str(child_id)] for child_id in (node.get("children") or []) if str(child_id) in nodes]


def _split_sentences(text):
    text = _display_text(text)
    if not text:
        return []
    pieces = re.split(r"(?<=[。！？；：])", text)
    sentences = []
    for piece in pieces:
        candidate = piece.strip(" \n\r\t,，")
        if candidate:
            sentences.append(candidate)
    return sentences


def _segment_points(segment, analysis=None, max_points=3, fallback_labels=None):
    payload = getattr(segment, "raw_payload", None) or {}
    candidates = []

    for item in (payload.get("bullet_points") or []):
        text = _trim_text(item, 20)
        if text and not _is_generic_label(text):
            candidates.append(text)

    for item in (payload.get("key_points") or []):
        text = _trim_text(item, 20)
        if text and not _is_generic_label(text):
            candidates.append(text)

    if not candidates and fallback_labels:
        for item in fallback_labels:
            text = _trim_text(item, 18)
            if text and not _is_generic_label(text):
                candidates.append(text)

    if not candidates:
        sentences = _split_sentences(getattr(segment, "voice_text", "") or "")
        for sentence in sentences:
            parts = re.split(r"[，,:：]", sentence)
            for part in parts:
                text = _trim_text(part, 18)
                if len(_normalize_label(text)) >= 4 and not _is_generic_label(text):
                    candidates.append(text)
            if len(candidates) >= max_points * 2:
                break

    if not candidates and analysis is not None:
        for item in (getattr(analysis, "key_points", None) or []):
            text = _trim_text(item, 18)
            if text and not _is_generic_label(text):
                candidates.append(text)

    unique = []
    seen = set()
    for item in candidates:
        key = _normalize_label(item)
        if not key or key in seen:
            continue
        unique.append(item)
        seen.add(key)
        if len(unique) >= max_points:
            break
    fallback = _meaningful_label(getattr(segment, "segment_title", ""), 18)
    return unique or [fallback or "核心观点"]


def _normalize_label(text):
    return re.sub(r"[\s，。、“”‘’：:；;,.!?！？（）()\[\]【】《》|·\-—_]+", "", str(text or "")).lower()


_GENERIC_LABEL_WORDS = {
    "主题",
    "中心主题",
    "当前主题",
    "当前节点",
    "当前段落",
    "当前章节",
    "本段",
    "本段落",
    "节点",
    "段落",
    "章节",
    "节目段落",
}


def _is_generic_label(value):
    text = _display_text(value)
    normalized = _normalize_label(text)
    if not normalized:
        return True
    if normalized in {_normalize_label(item) for item in _GENERIC_LABEL_WORDS}:
        return True
    number = r"[0-9一二三四五六七八九十百千万零〇]+"
    generic_patterns = [
        rf"^(节目)?段落{number}$",
        rf"^(节目)?章节{number}$",
        rf"^节目{number}$",
        rf"^section{number}$",
        rf"^program{number}$",
        rf"^node{number}$",
        rf"^s{number}$",
    ]
    return any(re.match(pattern, normalized, flags=re.IGNORECASE) for pattern in generic_patterns)


def _meaningful_label(value, limit, min_chars=4):
    text = _trim_text(value, limit)
    if not text or _is_generic_label(text):
        return ""
    if len(_normalize_label(text)) < min_chars:
        return ""
    return text


def _first_meaningful_clause(text, limit=24, min_chars=6):
    for sentence in _split_sentences(text):
        for clause in re.split(r"[，,；;：:。！？!?]", sentence):
            label = _meaningful_label(clause.strip(" \n\r\t-—:："), limit, min_chars=min_chars)
            if label:
                return label
    return ""


def _segment_display_title(segment, analysis=None, max_chars=24):
    payload = getattr(segment, "raw_payload", None) or {}
    candidates = [
        getattr(segment, "segment_title", None),
        payload.get("display_title"),
        payload.get("visual_title"),
        payload.get("headline"),
        payload.get("title"),
        payload.get("section_title"),
        payload.get("summary"),
        payload.get("segment_summary"),
    ]
    for value in candidates:
        label = _meaningful_label(value, max_chars)
        if label:
            return label

    clause = _first_meaningful_clause(getattr(segment, "voice_text", "") or "", limit=max_chars)
    if clause:
        return clause

    if analysis is not None:
        clause = _first_meaningful_clause(getattr(analysis, "core_view", "") or "", limit=max_chars)
        if clause:
            return clause

    detail = _segment_detail_text(segment, analysis=analysis, min_chars=12, max_chars=max_chars)
    label = _meaningful_label(detail, max_chars)
    if label:
        return label
    return _trim_text(getattr(segment, "segment_id", None) or "当前段落", max_chars)


def _node_display_label(node, fallback="", max_chars=24):
    label = _meaningful_label((node or {}).get("label"), max_chars)
    if label:
        return label
    return _trim_text(fallback, max_chars)


def _root_display_label(report, root, segment=None, max_chars=28):
    analysis = getattr(report, "analysis", None)
    for value in [
        (root or {}).get("label"),
        getattr(analysis, "theme", None),
        getattr(analysis, "report_title", None),
        getattr(report, "file_name", None),
    ]:
        label = _meaningful_label(value, max_chars)
        if label:
            return label
    if segment is not None:
        return _segment_display_title(segment, analysis=analysis, max_chars=max_chars)
    return "中心主题"


def _meaningful_child_labels(children, limit=18):
    labels = []
    for child in children or []:
        label = _meaningful_label((child or {}).get("label") or (child or {}).get("id"), limit)
        if label:
            labels.append(label)
    return labels


def _segment_visual_points(segment, analysis=None, max_points=3, fallback_labels=None):
    points = _segment_points(
        segment,
        analysis=analysis,
        max_points=max_points,
        fallback_labels=[label for label in (fallback_labels or []) if not _is_generic_label(label)],
    )
    points = [point for point in points if not _is_generic_label(point)]
    if points:
        return points[:max_points]
    return [_segment_display_title(segment, analysis=analysis, max_chars=18)]


def _subheading_text(analysis, segment):
    payload = getattr(segment, "raw_payload", None) or {}
    core_view = getattr(analysis, "core_view", None) or payload.get("core_view") or getattr(segment, "segment_title", "") or ""
    return _trim_text(core_view, 28)


def _segment_detail_text(segment, analysis=None, min_chars=60, max_chars=100):
    payload = getattr(segment, "raw_payload", None) or {}
    candidates = []
    best_short = ""
    for key in ("detail_text", "visual_explanation", "summary", "segment_summary", "source_evidence"):
        value = payload.get(key)
        if value:
            candidates.append(str(value))
    voice_text = getattr(segment, "voice_text", "") or ""
    if voice_text:
        candidates.append(voice_text)
    if analysis is not None and getattr(analysis, "core_view", None):
        candidates.append(str(getattr(analysis, "core_view")))

    for raw in candidates:
        text = re.sub(r"\s+", " ", raw).strip()
        if not text:
            continue
        sentences = _split_sentences(text)
        if not sentences:
            continue

        parts = []
        total = 0
        for sentence in sentences:
            clauses = re.split(r"(?<=[，；：])", sentence)
            for clause in clauses:
                clause = clause.strip(" ，,")
                if not clause:
                    continue
                if total + len(clause) > max_chars and total >= min_chars:
                    break
                parts.append(clause)
                total += len(clause)
                if total >= max_chars:
                    break
            if total >= min_chars:
                break

        detail = "".join(parts).strip(" ，,。；;：:")
        if min_chars <= len(detail) <= max_chars:
            return detail
        if len(detail) > max_chars:
            return detail[:max_chars].rstrip("，,。；;：: ")
        if len(detail) > len(best_short):
            best_short = detail

    if len(best_short) >= 18:
        return best_short

    fallback = _trim_text(getattr(segment, "segment_title", "") or "本段聚焦当前节点与相关逻辑。", max_chars)
    return fallback


def _title_parts(report_title, analysis=None, segment=None):
    report_title = re.sub(r"\s+", " ", str(report_title or "")).strip()
    separators = ["：", ":", "-", "|", "｜"]
    main_title = report_title
    tail = ""
    for separator in separators:
        if separator in report_title:
            left, right = report_title.split(separator, 1)
            if left.strip():
                main_title = left.strip()
                tail = right.strip()
                break
    main_title = _trim_text(main_title, 24)
    subtitle = tail or _subheading_text(analysis, segment)
    subtitle = _trim_text(subtitle, 24)
    return main_title, subtitle


def _safe_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _format_source_date(value):
    if not value:
        return ""
    token = str(value).strip()
    token = token.replace("/", "-").replace(".", "-").replace("_", "-")
    if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", token):
        return token
    if re.fullmatch(r"\d{8}", token):
        return f"{token[:4]}-{token[4:6]}-{token[6:8]}"
    if re.fullmatch(r"\d{6}", token):
        return f"20{token[:2]}-{token[2:4]}-{token[4:6]}"
    return token


def _extract_date_from_text(*values):
    patterns = [
        re.compile(r"(20\d{2}[-_.]?\d{2}[-_.]?\d{2})"),
        re.compile(r"(?<!\d)(\d{6})(?!\d)"),
    ]
    for value in values:
        text = str(value or "")
        for pattern in patterns:
            match = pattern.search(text)
            if match:
                return _format_source_date(match.group(1))
    return ""


def _clean_report_title(value, institution=""):
    title = Path(str(value or "")).stem
    title = re.sub(r"(20\d{2}[-_.]?\d{2}[-_.]?\d{2}|\d{6})$", "", title).strip(" -_|｜")
    if institution:
        prefix_patterns = [
            rf"^{re.escape(institution)}\s*[-—:：|｜]\s*",
            rf"^{re.escape(institution)}",
        ]
        for pattern in prefix_patterns:
            title = re.sub(pattern, "", title).strip(" -_|｜")
    return re.sub(r"\s+", " ", title).strip()


def _clean_institution_name(value):
    text = re.sub(r"\s+", " ", str(value or "")).strip(" -_|｜，,。;；")
    if not text:
        return ""
    known_patterns = [
        (r"伯恩斯坦|Bernstein", "伯恩斯坦"),
        (r"高盛|Goldman", "高盛"),
        (r"摩根士丹利|Morgan Stanley", "摩根士丹利"),
        (r"瑞银|UBS", "瑞银"),
        (r"花旗|Citi", "花旗"),
        (r"美银|BofA|Bank of America", "美银"),
        (r"摩根大通|JPMorgan|J\.P\. Morgan", "摩根大通"),
        (r"中金|CICC", "中金"),
        (r"华泰|HTSC", "华泰"),
    ]
    for pattern, name in known_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return name
    text = re.split(r"\s+(?:是|为|作为)\s*|[，,。;；]", text, maxsplit=1)[0].strip()
    if len(text) > 18:
        text = text[:18].rstrip(" -_|｜，,。;；")
    return text


def _source_page_number(report, segment=None):
    payload = getattr(segment, "raw_payload", None) or {}
    page = (
        payload.get("source_page")
        or payload.get("source_page_num")
        or payload.get("page")
        or getattr(getattr(report, "analysis", None), "raw_payload", {}).get("source_page")
        or 1
    )
    return max(_safe_int(page, 1) or 1, 1)


def _report_source_meta(report, segment=None):
    analysis = getattr(report, "analysis", None)
    file_name = getattr(report, "file_name", "") or ""
    institution = _clean_institution_name(getattr(analysis, "institution", None))
    if not institution:
        base = Path(file_name).stem
        head = re.split(r"[-—|｜:：]", base, maxsplit=1)[0].strip()
        institution = _clean_institution_name(head) if 1 < len(head) <= 30 else ""

    raw_title = getattr(analysis, "report_title", None) or file_name
    title = _clean_report_title(raw_title, institution=institution) or _clean_report_title(file_name, institution=institution) or "研报标题"
    date_text = _extract_date_from_text(file_name, raw_title, getattr(segment, "segment_title", ""))
    theme = (getattr(analysis, "theme", None) or "").strip()
    core_view = (getattr(analysis, "core_view", None) or "").strip()
    if not core_view and getattr(segment, "voice_text", None):
        core_view = _segment_detail_text(segment, analysis=analysis, min_chars=36, max_chars=56)

    source_page = _source_page_number(report, segment=segment)
    source_line_parts = []
    if institution:
        source_line_parts.append(institution)
    if title:
        source_line_parts.append(f"《{_trim_text(title, 30)}》")
    source_line = "来源研报：" + "".join(source_line_parts) if source_line_parts else "来源研报"
    if date_text:
        source_line += f"｜{date_text}"

    return {
        "institution": institution or "机构研报",
        "title": title,
        "date": date_text,
        "theme": theme,
        "core_view": _trim_text(core_view or title, 46),
        "source_line": source_line,
        "source_page": source_page,
        "page_label": f"第 {source_page} 页" if source_page > 1 else "封面页",
    }


def _report_pdf_preview_path(report, page_number=1):
    pdf_path = PROJECT_ROOT / getattr(report, "file_path", "")
    return pdf_path.parent / "preview_cache" / f"page_{max(int(page_number), 1):03d}.jpg"


def _resolve_local_path(value):
    if not value:
        return None
    path = Path(str(value))
    return path if path.is_absolute() else PROJECT_ROOT / path


def _find_pdf_by_filename(filename):
    filename = str(filename or "").strip()
    if not filename:
        return None
    if filename in _PDF_FILENAME_CACHE:
        return _PDF_FILENAME_CACHE[filename]

    for root in [PROJECT_ROOT / "data/tasks", PROJECT_ROOT / "data/zsxq"]:
        if not root.exists():
            continue
        try:
            for path in root.rglob("*.pdf"):
                if path.name == filename:
                    _PDF_FILENAME_CACHE[filename] = path
                    return path
        except OSError:
            continue
    _PDF_FILENAME_CACHE[filename] = None
    return None


def _ensure_pdf_path_preview(pdf_path, page_number=1):
    pdf_path = _resolve_local_path(pdf_path)
    if not pdf_path or not pdf_path.exists() or not pdf_path.is_file() or pdf_path.suffix.lower() != ".pdf":
        return None

    preview_path = pdf_path.parent / "preview_cache" / f"page_{max(int(page_number), 1):03d}.jpg"
    if preview_path.exists():
        return preview_path

    preview_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with fitz.open(str(pdf_path)) as doc:
            if not doc.page_count:
                return None
            page_index = max(0, min(int(page_number) - 1, doc.page_count - 1))
            page = doc.load_page(page_index)
            pix = page.get_pixmap(matrix=fitz.Matrix(1.35, 1.35), alpha=False)
            pix.save(str(preview_path))
    except Exception:
        return None
    return preview_path if preview_path.exists() else None


def _ensure_report_pdf_preview(report, page_number=1):
    return _ensure_pdf_path_preview(getattr(report, "file_path", ""), page_number=page_number)


def _load_report_pdf_preview(report, page_number=1, target_size=None):
    preview_path = _ensure_report_pdf_preview(report, page_number=page_number)
    if not preview_path:
        return None
    try:
        image = Image.open(preview_path).convert("RGB")
    except Exception:
        return None

    if target_size:
        image.thumbnail(target_size, Image.Resampling.LANCZOS)
    return image


def _load_pdf_path_preview(pdf_path, page_number=1, target_size=None):
    preview_path = _ensure_pdf_path_preview(pdf_path, page_number=page_number)
    if not preview_path:
        return None
    try:
        image = Image.open(preview_path).convert("RGB")
    except Exception:
        return None
    if target_size:
        image.thumbnail(target_size, Image.Resampling.LANCZOS)
    return image


def _program_package_source_index(segment, source_count):
    payload = getattr(segment, "raw_payload", None) or {}
    for key in ("source_report_index", "item_index", "report_index"):
        index = _safe_int(payload.get(key), None)
        if index is not None:
            return max(0, min(index - 1 if index >= 1 else index, source_count - 1))

    for value in [
        payload.get("voice_text_ref"),
        payload.get("section_id"),
        payload.get("section_type"),
        getattr(segment, "segment_id", None),
    ]:
        match = re.search(r"item[_-](\d+)", str(value or ""), flags=re.IGNORECASE)
        if match:
            return max(0, min(int(match.group(1)) - 1, source_count - 1))

    segment_index = _safe_int(getattr(segment, "segment_index", None), None)
    if segment_index is not None and segment_index >= 3:
        return max(0, min((segment_index - 3) // 3, source_count - 1))
    return 0


def _program_package_source_pdf_path(report, segment=None):
    package_path = _resolve_local_path(getattr(report, "file_path", ""))
    if not package_path or package_path.suffix.lower() != ".json" or not package_path.exists():
        return None
    try:
        data = json.loads(package_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    source_reports = data.get("source_reports") if isinstance(data.get("source_reports"), list) else []
    if not source_reports:
        return None

    start_index = _program_package_source_index(segment, len(source_reports)) if segment is not None else 0
    ordered_indexes = [start_index] + [idx for idx in range(len(source_reports)) if idx != start_index]
    for index in ordered_indexes:
        item = source_reports[index] if 0 <= index < len(source_reports) else {}
        pdf = item.get("pdf") if isinstance(item, dict) else {}
        pdf_path = (pdf or {}).get("path") or item.get("pdf_path") or item.get("file_path")
        resolved = _resolve_local_path(pdf_path)
        if resolved and resolved.exists() and resolved.suffix.lower() == ".pdf":
            return resolved
        filename = (pdf or {}).get("filename") or item.get("filename") or (Path(str(pdf_path)).name if pdf_path else "")
        resolved = _find_pdf_by_filename(filename)
        if resolved and resolved.exists() and resolved.suffix.lower() == ".pdf":
            return resolved
    return None


def _load_portrait_source_preview(report, segment, page_number=1):
    preview = _load_report_pdf_preview(report, page_number=page_number)
    if preview is not None:
        return preview
    program_pdf_path = _program_package_source_pdf_path(report, segment=segment)
    if program_pdf_path:
        return _load_pdf_path_preview(program_pdf_path, page_number=1)
    return None


def _paste_rounded_image(base_img, image, box, radius=18, background="#0f172a"):
    if image is None:
        return False
    x1, y1, x2, y2 = [int(value) for value in box]
    target_w = max(x2 - x1, 1)
    target_h = max(y2 - y1, 1)
    canvas = Image.new("RGB", (target_w, target_h), background)
    thumb = image.copy()
    thumb.thumbnail((target_w, target_h), Image.Resampling.LANCZOS)
    paste_x = (target_w - thumb.width) // 2
    paste_y = (target_h - thumb.height) // 2
    canvas.paste(thumb, (paste_x, paste_y))
    mask = Image.new("L", (target_w, target_h), 0)
    mask_draw = ImageDraw.Draw(mask)
    mask_draw.rounded_rectangle((0, 0, target_w, target_h), radius=radius, fill=255)
    base_img.paste(canvas, (x1, y1), mask)
    return True


def _source_intro_duration(duration):
    duration = max(float(duration or 0.0), 0.0)
    if duration < 2.6:
        return 0.0
    return round(min(DEFAULT_SOURCE_CARD_DURATION_SEC, max(duration - 1.0, 0.0)), 3)


def _source_card_duration_for(report=None, task=None, multi_report=False):
    payload = {}
    if getattr(task, "result", None):
        payload.update(task.result or {})
    analysis = getattr(report, "analysis", None)
    if getattr(analysis, "raw_payload", None):
        payload.update(analysis.raw_payload or {})
    if getattr(report, "raw_meta", None):
        payload.update(report.raw_meta or {})

    important = bool(
        payload.get("important_report")
        or payload.get("is_important_report")
        or payload.get("source_card_important")
    )
    default_value = payload.get("source_card_duration_sec")
    important_value = payload.get("source_card_duration_sec_for_important_report")

    try:
        duration = float(important_value if important and important_value is not None else default_value)
    except (TypeError, ValueError):
        duration = IMPORTANT_SOURCE_CARD_DURATION_SEC if important else DEFAULT_SOURCE_CARD_DURATION_SEC
    if multi_report and duration < 4.0:
        duration = 4.0
    return round(max(duration, 0.0), 3)


def _source_card_visual_states(duration):
    duration = max(float(duration or 0.0), 0.0)
    if duration <= 0:
        return []
    checkpoints = [0.18, 0.42, 0.68, 1.0]
    if duration < 2.4:
        checkpoints = [0.35, 0.7, 1.0]
    step_duration = duration / len(checkpoints)
    return [
        {
            "reveal_progress": checkpoint,
            "duration": round(step_duration, 3),
        }
        for checkpoint in checkpoints
    ]


def _topic_planning_result(task):
    return ((getattr(task, "result", None) or {}).get("topic_planning") or {}) if task else {}


def _active_topic_bundle(task):
    planning = _topic_planning_result(task)
    topic_id = planning.get("applied_topic_id") or planning.get("active_topic_id")
    topics = planning.get("topics") or []
    topic = next((item for item in topics if item.get("topic_id") == topic_id), None) or {}
    script = (planning.get("topic_scripts") or {}).get(topic_id) if topic_id else None
    return topic_id, topic, script if isinstance(script, dict) else {}


def _is_theme_task(task):
    topic_id, _, script = _active_topic_bundle(task)
    return bool(topic_id and script)


def _is_theme_segment(report, segment):
    payload = getattr(segment, "raw_payload", None) or {}
    return bool(payload.get("topic_id") or _is_theme_task(getattr(report, "task", None)))


def _normalize_report_code(value):
    token = str(value or "").strip().upper()
    if token.startswith("R"):
        token = token[1:]
    if token.isdigit():
        return f"{int(token):03d}"
    return token


def _theme_source_reports(task, limit=8):
    if not task:
        return []
    _, topic, script = _active_topic_bundle(task)
    wanted = []
    wanted.extend(topic.get("reports") or [])
    for section in script.get("sections") or []:
        if isinstance(section, dict):
            wanted.extend(section.get("source_report_ids") or [])
    normalized = [_normalize_report_code(item) for item in wanted if item]
    reports = []
    if normalized:
        for code in normalized:
            report = next((item for item in task.reports if _normalize_report_code(item.report_code) == code), None)
            if report and report not in reports:
                reports.append(report)
    for report in task.reports:
        if report not in reports:
            reports.append(report)
    return reports[:limit]


def _looks_truncated_text(value):
    text = str(value or "").strip()
    return bool(re.search(r"(\.{3,}|…+)\s*$", text))


def _strip_trailing_ellipsis(value):
    text = str(value or "").strip()
    text = re.sub(r"(\.{3,}|…+)\s*$", "", text).strip(" ，,。;；")
    return f"{text}。" if text else ""


def _opening_section_text(script):
    sections = script.get("sections") if isinstance(script.get("sections"), list) else []
    if not sections:
        return ""
    opening_section = next(
        (
            section
            for section in sections
            if str(section.get("section_type") or "").lower() in {"topic_opening", "opening"}
            or str(section.get("focus_node_id") or "") == "node_overview"
        ),
        sections[0],
    )
    return str(
        opening_section.get("voice_text")
        or opening_section.get("visual_explanation")
        or opening_section.get("section_title")
        or ""
    ).strip()


def _theme_summary(task):
    topic_id, topic, script = _active_topic_bundle(task)
    source_card = script.get("source_card") or {}
    reports = _theme_source_reports(task, limit=12)
    institutions = []
    for item in (source_card.get("main_institutions") or topic.get("institutions") or []):
        text = _clean_institution_name(item)
        if text and text not in institutions:
            institutions.append(text)
    for report in reports:
        institution = _report_source_meta(report).get("institution")
        if institution and institution != "机构研报" and institution not in institutions:
            institutions.append(institution)

    tags = []
    for value in [
        topic.get("topic_category"),
        topic.get("topic_name"),
        *(source_card.get("focus_points") or []),
        *(topic.get("common_views") or []),
    ]:
        text = _trim_text(value, 12)
        if text and text not in tags:
            tags.append(text)
    report_count = int(source_card.get("report_count") or topic.get("report_count") or len(reports) or 0)
    opening = script.get("opening_text") or topic.get("recommended_video_angle") or topic.get("topic_summary") or ""
    section_opening = _opening_section_text(script)
    if section_opening and (_looks_truncated_text(opening) or len(str(opening or "")) < 40):
        opening = section_opening
    if _looks_truncated_text(opening):
        opening = _strip_trailing_ellipsis(opening)
    if len(opening) < 40:
        opening = (
            f"今天这批研报共同指向的是：{topic.get('topic_name') or script.get('video_title') or '当前主题'}。"
            f"这不是单篇观点，而是多家机构反复关注的一条主线。"
        )
    return {
        "topic_id": topic_id,
        "topic_name": topic.get("topic_name") or script.get("video_title") or "主题研报解读",
        "video_title": script.get("video_title") or topic.get("recommended_title") or topic.get("topic_name") or "主题研报解读",
        "video_subtitle": script.get("video_subtitle") or topic.get("topic_summary") or "",
        "opening_text": opening,
        "institutions": institutions,
        "report_count": report_count,
        "institution_count": len(institutions),
        "tags": tags[:6],
        "source_line": f"联合来源：{'、'.join(institutions[:4]) or '多家机构'}｜{report_count}份研报",
    }


def _theme_flow_items(task, report=None):
    _, topic, script = _active_topic_bundle(task or getattr(report, "task", None))
    flow = script.get("theme_flow") if isinstance(script.get("theme_flow"), list) else []
    sections = script.get("sections") if isinstance(script.get("sections"), list) else []
    if not flow and sections:
        flow = [
            {
                "id": section.get("focus_node_id") or f"node_{idx:03d}",
                "label": section.get("section_title") or f"章节 {idx}",
                "section_id": section.get("section_id") or f"S{idx:03d}",
                "section_type": section.get("section_type") or "topic_section",
            }
            for idx, section in enumerate(sections)
            if isinstance(section, dict)
        ]
    if not flow and report is not None:
        flow = [
            {
                "id": segment.focus_node_id or segment.segment_id,
                "label": _segment_display_title(segment, analysis=getattr(report, "analysis", None), max_chars=18),
                "section_id": segment.segment_id,
                "section_type": segment.segment_type or "topic_section",
            }
            for segment in getattr(report, "segments", [])
        ]
    if not flow:
        flow = [{"id": "node_overview", "label": topic.get("topic_name") or "主题总开场", "section_id": "S000", "section_type": "topic_opening"}]
    normalized = []
    seen = set()
    for index, item in enumerate(flow[:8]):
        if not isinstance(item, dict):
            continue
        item_id = str(item.get("id") or f"node_{index:03d}")
        if item_id in seen:
            continue
        seen.add(item_id)
        normalized.append({
            "id": item_id,
            "label": _trim_text(item.get("label") or f"章节 {index + 1}", 18),
            "section_id": item.get("section_id") or f"S{index:03d}",
            "section_type": item.get("section_type") or "topic_section",
        })
    return normalized


def _theme_opening_visual_states(task, duration=None):
    duration = max(float(duration or 0.0), 0.0)
    if duration <= 0:
        duration = 12.0

    evidence_duration = min(duration, 12.0)
    covers = evidence_duration * 0.78
    summary = evidence_duration - covers
    hook = max(duration - evidence_duration, 0.0)
    states = [
        {"stage": "covers", "progress": 0.28, "duration": round(covers * 0.34, 3)},
        {"stage": "covers", "progress": 0.72, "duration": round(covers * 0.33, 3)},
        {"stage": "covers", "progress": 1.0, "duration": round(covers * 0.33, 3)},
        {"stage": "summary", "progress": 1.0, "duration": round(summary, 3)},
    ]
    if hook >= 0.6:
        states.append({"stage": "hook", "progress": 1.0, "duration": round(hook, 3)})
    return states


def _seconds_to_ass_time(value):
    value = max(float(value or 0.0), 0.0)
    hours = int(value // 3600)
    minutes = int((value % 3600) // 60)
    seconds = value % 60
    return f"{hours}:{minutes:02d}:{seconds:05.2f}"


def _seconds_to_srt_time(value):
    value = max(float(value or 0), 0)
    hours = int(value // 3600)
    minutes = int((value % 3600) // 60)
    seconds = int(value % 60)
    millis = int(round((value - int(value)) * 1000))
    if millis >= 1000:
        seconds += 1
        millis -= 1000
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


def _parse_srt_time(value):
    hours, minutes, seconds = str(value).replace(".", ",").split(":")
    sec, millis = seconds.split(",", 1)
    return int(hours) * 3600 + int(minutes) * 60 + int(sec) + int(millis[:3]) / 1000


def _shift_time_by_inserts(value, inserts):
    original = float(value or 0.0)
    shifted = original
    for item in inserts or []:
        if original >= float(item.get("insert_at", 0.0)):
            shifted += float(item.get("duration", 0.0))
    return shifted


def _shift_srt_by_inserts(subtitle_path, inserts, output_path):
    subtitle_path = Path(subtitle_path)
    if not subtitle_path.exists() or not inserts:
        return subtitle_path

    blocks = re.split(r"\n\s*\n", subtitle_path.read_text(encoding="utf-8", errors="ignore").strip())
    shifted_blocks = []
    for block in blocks:
        lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        if len(lines) < 2 or "-->" not in lines[1]:
            continue
        start_raw, end_raw = [part.strip() for part in lines[1].split("-->", 1)]
        start_sec = _shift_time_by_inserts(_parse_srt_time(start_raw), inserts)
        end_sec = _shift_time_by_inserts(_parse_srt_time(end_raw), inserts)
        shifted_blocks.append(
            "\n".join([
                lines[0],
                f"{_seconds_to_srt_time(start_sec)} --> {_seconds_to_srt_time(end_sec)}",
                *lines[2:],
            ])
        )

    output_path = Path(output_path)
    output_path.write_text("\n\n".join(shifted_blocks) + ("\n" if shifted_blocks else ""), encoding="utf-8")
    return output_path


def _parse_srt_entries(subtitle_path):
    subtitle_path = Path(subtitle_path)
    if not subtitle_path.exists():
        return []

    entries = []
    blocks = re.split(r"\n\s*\n", subtitle_path.read_text(encoding="utf-8", errors="ignore").strip())
    for block in blocks:
        lines = [line.rstrip() for line in block.splitlines() if line.strip()]
        if len(lines) < 2 or "-->" not in lines[1]:
            continue
        try:
            start_raw, end_raw = [part.strip() for part in lines[1].split("-->", 1)]
            start_sec = _parse_srt_time(start_raw)
            end_sec = _parse_srt_time(end_raw)
        except (ValueError, IndexError):
            continue
        text = "\n".join(lines[2:]).strip()
        if not text or end_sec <= start_sec:
            continue
        entries.append({
            "start_sec": start_sec,
            "end_sec": end_sec,
            "text": text,
        })
    return sorted(entries, key=lambda item: (item["start_sec"], item["end_sec"]))


def _active_subtitle_text(subtitle_entries, timestamp):
    active = [
        item for item in subtitle_entries
        if float(item["start_sec"]) <= timestamp < float(item["end_sec"])
    ]
    if not active:
        return ""
    return active[-1]["text"]


def _draw_subtitle_overlay_on_frame(frame_path, output_path, text, width, height):
    text = _display_text(text)
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s*\n\s*(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"\s+", " ", text.replace("\n", " ")).strip()
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[\u4e00-\u9fff])", "", text)
    text = re.sub(r"(?<=[\u4e00-\u9fff])\s+(?=[，。！？；：、,.!?;:%％])", "", text)
    text = re.sub(r"(?<=[，。！？；：、,.!?;:])\s+(?=[\u4e00-\u9fff])", "", text)
    if not text:
        return frame_path

    img = Image.open(frame_path).convert("RGBA")
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    font = load_font(36)
    max_width = int(width * 0.72)
    lines = wrap_text(draw, text, font, max_width, max_lines=2)
    if not lines:
        return frame_path

    line_gap = 45
    block_height = len(lines) * line_gap
    subtitle_band_top = height - 188
    y = subtitle_band_top + 60 + max(0, (98 - block_height) / 2)
    bar_x1 = int(width * 0.16)
    bar_x2 = int(width * 0.84)
    bar_y1 = int(y - 15)
    bar_y2 = int(y + block_height + 8)
    draw.rounded_rectangle(
        (bar_x1, bar_y1, bar_x2, bar_y2),
        radius=20,
        fill=(2, 8, 18, 126),
    )
    draw.line((bar_x1 + 34, bar_y1 + 2, bar_x2 - 34, bar_y1 + 2), fill=(250, 204, 21, 110), width=2)
    draw.line((bar_x1 + 34, bar_y2 - 1, bar_x2 - 34, bar_y2 - 1), fill=(148, 163, 184, 42), width=1)
    for idx, line in enumerate(lines):
        _draw_rich_centered_text(draw, line, y + idx * line_gap, font, width)
    composed = Image.alpha_composite(img, overlay)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    composed.convert("RGB").save(output_path, quality=95)
    return output_path


def _burn_subtitles_into_frame_items(frame_items, subtitle_path, output_dir, width, height):
    subtitle_entries = _parse_srt_entries(subtitle_path)
    if not subtitle_entries:
        return frame_items

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    new_items = []
    render_cache = {}
    cursor = 0.0
    rendered_index = 0

    for frame_path, duration in frame_items:
        duration = max(float(duration or 0.0), 0.0)
        start = cursor
        end = start + duration
        cursor = end
        if duration <= 0:
            continue

        cuts = [start, end]
        for entry in subtitle_entries:
            entry_start = float(entry["start_sec"])
            entry_end = float(entry["end_sec"])
            if entry_end <= start + 0.01 or entry_start >= end - 0.01:
                continue
            cuts.append(max(start, entry_start))
            cuts.append(min(end, entry_end))
        cuts = sorted({round(value, 3) for value in cuts if start - 0.001 <= value <= end + 0.001})

        for cut_start, cut_end in zip(cuts, cuts[1:]):
            piece_duration = max(cut_end - cut_start, 0.0)
            if piece_duration < 0.03:
                continue
            mid = cut_start + piece_duration / 2
            subtitle_text = _active_subtitle_text(subtitle_entries, mid)
            if subtitle_text:
                cache_key = (str(Path(frame_path).resolve()), subtitle_text)
                subtitle_frame = render_cache.get(cache_key)
                if subtitle_frame is None:
                    rendered_index += 1
                    subtitle_frame = output_dir / f"subtitle_frame_{rendered_index:05d}.jpg"
                    _draw_subtitle_overlay_on_frame(frame_path, subtitle_frame, subtitle_text, width, height)
                    render_cache[cache_key] = subtitle_frame
                new_items.append((subtitle_frame, piece_duration))
            else:
                new_items.append((frame_path, piece_duration))

    return new_items or frame_items


def _concat_audio_with_silence_inserts(audio_path, inserts, output_path):
    audio_path = Path(audio_path)
    if not audio_path.exists() or not inserts:
        return audio_path

    total_duration = media_duration(audio_path)
    if not total_duration or total_duration <= 0:
        return audio_path
    profile = audio_stream_profile(audio_path)
    sample_rate = int(profile.get("sample_rate") or 44100)
    channels = max(int(profile.get("channels") or 2), 1)
    channel_layout = str(profile.get("channel_layout") or ("mono" if channels == 1 else "stereo"))

    inserts = sorted(
        [
            {
                "insert_at": max(0.0, min(float(item.get("insert_at") or 0.0), total_duration)),
                "duration": max(float(item.get("duration") or 0.0), 0.0),
            }
            for item in inserts
            if float(item.get("duration") or 0.0) > 0
        ],
        key=lambda item: item["insert_at"],
    )
    if not inserts:
        return audio_path

    temp_dir = Path(output_path).with_suffix("")
    temp_dir.mkdir(parents=True, exist_ok=True)
    concat_items = []
    cursor = 0.0
    part_index = 0

    for item in inserts:
        insert_at = item["insert_at"]
        duration = item["duration"]
        if insert_at > cursor + 0.05:
            segment_path = temp_dir / f"part_{part_index:03d}.m4a"
            subprocess.run(
                [
                    "ffmpeg",
                    "-y",
                    "-ss",
                    f"{cursor:.3f}",
                    "-i",
                    str(audio_path),
                    "-t",
                    f"{max(insert_at - cursor, 0.05):.3f}",
                    "-vn",
                    "-c:a",
                    "aac",
                    "-ar",
                    str(sample_rate),
                    "-ac",
                    str(channels),
                    str(segment_path),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=240,
            )
            concat_items.append(segment_path)
            part_index += 1

        silence_path = temp_dir / f"silence_{part_index:03d}.m4a"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"anullsrc=r={sample_rate}:cl={channel_layout}",
                "-t",
                f"{duration:.3f}",
                "-c:a",
                "aac",
                "-ar",
                str(sample_rate),
                "-ac",
                str(channels),
                str(silence_path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        concat_items.append(silence_path)
        part_index += 1
        cursor = insert_at

    if cursor < total_duration - 0.05:
        tail_path = temp_dir / f"part_{part_index:03d}.m4a"
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-ss",
                f"{cursor:.3f}",
                "-i",
                str(audio_path),
                "-t",
                f"{max(total_duration - cursor, 0.05):.3f}",
                "-vn",
                "-c:a",
                "aac",
                "-ar",
                str(sample_rate),
                "-ac",
                str(channels),
                str(tail_path),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=240,
        )
        concat_items.append(tail_path)

    list_path = temp_dir / "concat.txt"
    list_path.write_text(
        "\n".join(f"file {shlex.quote(str(item.resolve()))}" for item in concat_items),
        encoding="utf-8",
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(list_path),
            "-c:a",
            "aac",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=240,
    )
    return Path(output_path)


def _expand_timeline_spans_with_source_cards(task, timeline_spans, segment_map, segments):
    if not timeline_spans:
        return timeline_spans, [], 0.0

    expanded = []
    inserts = []
    cumulative_offset = 0.0
    seen_reports = set()
    multi_report = len({segment.report_id for segment in segments if getattr(segment, "report_id", None)}) > 1

    for span in timeline_spans:
        segment = segment_map.get(span.get("segment_id"))
        report = segment.report if segment else _report_for_context_span(task, span, segments)
        report_id = getattr(report, "id", None)
        start_sec = float(span.get("start_sec") or 0.0)
        end_sec = float(span.get("end_sec") or start_sec + 1.0)

        if report and report_id not in seen_reports:
            card_duration = _source_card_duration_for(report=report, task=task, multi_report=multi_report)
            if card_duration > 0:
                inserts.append({
                    "insert_at": round(start_sec, 3),
                    "duration": round(card_duration, 3),
                    "report_id": report_id,
                })
                expanded.append({
                    "type": "source_card",
                    "report_id": report_id,
                    "segment_id": span.get("segment_id"),
                    "start_sec": round(start_sec + cumulative_offset, 3),
                    "end_sec": round(start_sec + cumulative_offset + card_duration, 3),
                    "text": "",
                })
                cumulative_offset += card_duration
            seen_reports.add(report_id)

        expanded.append({
            **span,
            "start_sec": round(start_sec + cumulative_offset, 3),
            "end_sec": round(end_sec + cumulative_offset, 3),
            "report_id": report_id,
        })

    return expanded, inserts, cumulative_offset


def _focus_children_for_render(nodes, root, focus_node):
    children = _node_children(nodes, focus_node)
    if children:
        return children[:3]

    root_children = _node_children(nodes, root)
    fallback = [node for node in root_children if str(node.get("id")) != str((focus_node or {}).get("id"))]
    return fallback[:3]


def _related_side_nodes(nodes, root, focus_node, child_nodes):
    child_ids = {str(node.get("id")) for node in child_nodes}
    related = {str((root or {}).get("id")), str((focus_node or {}).get("id"))} | child_ids
    candidates = []
    for node in nodes.values():
        node_id = str(node.get("id"))
        if node_id in related:
            continue
        candidates.append(node)
    return candidates[:4]


def _ease_in_out(value):
    value = max(0.0, min(float(value or 0.0), 1.0))
    return value * value * (3 - 2 * value)


def _root_branch_id(nodes, root, node_id):
    if not node_id or not root:
        return None
    root_children = {str(child_id) for child_id in (root.get("children") or [])}
    current_id = str(node_id)
    if current_id in root_children:
        return current_id

    visited = set()
    while current_id and current_id not in visited:
        visited.add(current_id)
        parent = _parent_node_for(nodes, current_id)
        if not parent:
            return None
        parent_id = str(parent.get("id"))
        if parent_id in root_children:
            return parent_id
        current_id = parent_id
    return None


def _branch_anchor_position(nodes, root, node_id, center_x, child_y):
    root_children = _node_children(nodes, root)
    if not root_children:
        return center_x, child_y

    branch_id = _root_branch_id(nodes, root, node_id) or str(root_children[min(len(root_children) - 1, 0)].get("id"))
    child_count = len(root_children)
    if child_count == 1:
        positions = [center_x]
    elif child_count == 2:
        positions = [center_x - 164, center_x + 164]
    elif child_count == 3:
        positions = [center_x - 220, center_x, center_x + 220]
    else:
        positions = [center_x - 300, center_x - 100, center_x + 100, center_x + 300][:child_count]

    index_map = {str(node.get("id")): idx for idx, node in enumerate(root_children)}
    branch_index = index_map.get(str(branch_id), min(len(positions) - 1, 0))
    return positions[branch_index], child_y


def _layout_text_block(draw, x, y, width, lines, font, fill, line_gap):
    for idx, line in enumerate(lines or []):
        draw.text((x, y + idx * line_gap), line, fill=fill, font=font)


def _draw_bullet_list(draw, x, y, points, font, line_gap, text_fill="#e5edf8", bullet_fill="#60a5fa"):
    for idx, point in enumerate(points or []):
        top = y + idx * line_gap
        bullet_x = x
        draw.ellipse((bullet_x, top + 14, bullet_x + 12, top + 26), fill=bullet_fill)
        draw.text((bullet_x + 28, top), point, fill=text_fill, font=font)


def _cover_image(image, target_size):
    target_w, target_h = target_size
    source = image.convert("RGB")
    if source.width <= 0 or source.height <= 0:
        return Image.new("RGB", target_size, "#070b14")
    scale = max(target_w / source.width, target_h / source.height)
    resized = source.resize(
        (max(int(source.width * scale + 0.5), target_w), max(int(source.height * scale + 0.5), target_h)),
        Image.Resampling.LANCZOS,
    )
    left = max((resized.width - target_w) // 2, 0)
    top = max((resized.height - target_h) // 2, 0)
    return resized.crop((left, top, left + target_w, top + target_h))


def _draw_text_with_shadow(draw, xy, text, font, fill, shadow=(0, 0, 0, 150), offset=(3, 4)):
    x, y = xy
    draw.text((x + offset[0], y + offset[1]), text, fill=shadow, font=font)
    draw.text((x, y), text, fill=fill, font=font)


def _portrait_brand_label(report):
    task = getattr(report, "task", None)
    result = getattr(task, "result", None) or {}
    candidates = [
        result.get("brand_text"),
        result.get("channel_name"),
        result.get("publisher_name"),
        result.get("creator_name"),
        (result.get("bilibili") or {}).get("account_name") if isinstance(result.get("bilibili"), dict) else None,
    ]
    for value in candidates:
        label = _display_text(value)
        if label:
            return _trim_text(label, 16)
    return "君君播客"


def _portrait_title_parts(report, source_meta):
    analysis = getattr(report, "analysis", None)
    raw_title = _display_text(
        source_meta.get("title")
        or getattr(analysis, "report_title", "")
        or getattr(report, "file_name", "")
        or "研报解读"
    )
    raw_title = Path(raw_title).stem
    raw_title = re.sub(r"\s+", " ", raw_title).strip(" -_|｜")
    for separator in ["：", ":", "｜", "|", " - ", "-", "—"]:
        if separator not in raw_title:
            continue
        left, right = [part.strip(" -_|｜") for part in raw_title.split(separator, 1)]
        if len(_normalize_label(left)) >= 3 and len(_normalize_label(right)) >= 3:
            return _trim_text(left, 22), _trim_text(right, 24)
    return _trim_text(raw_title, 32), ""


def _draw_portrait_editorial_backdrop(img, draw, width, height, subtitle_top, source_image=None):
    if source_image is not None:
        background = _cover_image(source_image, (width, height))
        soft = background.filter(ImageFilter.GaussianBlur(radius=5))
        background = Image.blend(background, soft, 0.38)
        background = ImageEnhance.Color(background).enhance(0.42)
        background = ImageEnhance.Contrast(background).enhance(0.88)
        background = ImageEnhance.Brightness(background).enhance(0.58)
        img.alpha_composite(background.convert("RGBA"))

    else:
        for y in range(0, height, 3):
            blend = y / max(height - 1, 1)
            r = int(5 + 4 * blend)
            g = int(8 + 8 * blend)
            b = int(17 + 15 * blend)
            draw.rectangle((0, y, width, y + 3), fill=(r, g, b, 255))

    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    for y in range(0, height, 4):
        t = y / max(height - 1, 1)
        alpha = int(28 + 220 * (t ** 1.42))
        overlay_draw.rectangle((0, y, width, y + 4), fill=(6, 11, 20, alpha))
    overlay_draw.rectangle((0, 0, width, 360), fill=(3, 7, 14, 72))
    overlay_draw.rectangle((0, subtitle_top - 64, width, height), fill=(3, 6, 14, 226))
    img.alpha_composite(overlay)

    draw.line((72, subtitle_top + 20, width - 72, subtitle_top + 20), fill=(148, 163, 184, 70), width=1)
    draw.line((0, subtitle_top, width, subtitle_top), fill=(255, 180, 170, 88), width=2)


def _draw_portrait_insight_rows(draw, left, top, right, points, font, small_font):
    row_h = 104
    gap = 10
    for idx, point in enumerate((points or [])[:3], start=1):
        y = top + (idx - 1) * (row_h + gap)
        icon = (left, y + 24, left + 58, y + 82)
        draw.ellipse(icon, fill=(255, 180, 170, 36), outline=(255, 180, 170, 94), width=2)
        number = f"{idx:02d}"
        bbox = draw.textbbox((0, 0), number, font=small_font)
        draw.text(
            (icon[0] + (icon[2] - icon[0] - (bbox[2] - bbox[0])) / 2, icon[1] + 16),
            str(idx),
            fill="#101827",
            font=small_font,
        )
        draw.text((left + 80, y + 14), f"指标 {number}", fill=PORTRAIT_PRIMARY, font=small_font)
        lines, render_font, _ = _adaptive_text_layout(draw, point, 34, 28, right - left - 100, 2)
        _layout_text_block(
            draw,
            left + 80,
            y + 50,
            right - left - 100,
            lines,
            render_font,
            "#f4f7fb",
            max(37, int(getattr(render_font, "size", 34) * 1.1)),
        )


def _fit_points_count(start_y, bottom_y, preferred_count=3, line_gap=64, min_count=1):
    usable = max(0, float(bottom_y) - float(start_y))
    count = int(usable // max(float(line_gap), 1.0)) + 1
    return max(min_count, min(int(preferred_count), count))


def _draw_region_grid(draw, region, step=72, color=(148, 163, 184, 26)):
    left, top, right, bottom = region
    for x in range(int(left), int(right), step):
        draw.line((x, top, x, bottom), fill=color, width=1)
    for y in range(int(top), int(bottom), step):
        draw.line((left, y, right, y), fill=color, width=1)


def _measure_text_block(draw, text, font, max_width, max_lines):
    lines = wrap_text(draw, text, font, max_width, max_lines)
    widths = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        widths.append(bbox[2] - bbox[0])
    return lines, max(widths) if widths else 0


def _line_visual_length(text):
    cleaned = re.sub(r"\s+", "", str(text or ""))
    cleaned = re.sub(r"[，。、“”‘’：:；;,.!?！？（）()\[\]【】《》|·\-—_]+", "", cleaned)
    return len(cleaned)


def _rebalance_wrapped_lines(draw, lines, font, max_width):
    lines = list(lines or [])
    if len(lines) != 2:
        return lines

    first, second = lines
    if _line_visual_length(second) >= 3:
        return lines

    while len(first) > 3 and _line_visual_length(second) < 3:
        candidate_first = first[:-1]
        candidate_second = first[-1] + second
        first_width = draw.textbbox((0, 0), candidate_first, font=font)[2]
        second_width = draw.textbbox((0, 0), candidate_second, font=font)[2]
        if first_width <= max_width and second_width <= max_width:
            first = candidate_first
            second = candidate_second
            continue
        break

    return [first, second]


def _adaptive_text_layout(draw, text, base_size, min_size, max_width, max_lines=2):
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        font = load_font(base_size)
        return [], font, 0

    best = None
    for font_size in range(base_size, min_size - 1, -2):
        font = load_font(font_size)
        lines, measured_width = _measure_text_block(draw, text, font, max_width, max_lines)
        lines = _rebalance_wrapped_lines(draw, lines, font, max_width)
        measured_width = max(
            (draw.textbbox((0, 0), line, font=font)[2] for line in lines),
            default=0,
        )
        if not lines:
            continue

        overflow = len("".join(lines).replace("...", "")) < len(text)
        line_lengths = [_line_visual_length(line) for line in lines]
        last_line_len = line_lengths[-1] if line_lengths else 0
        balance_gap = abs(line_lengths[0] - line_lengths[-1]) if len(line_lengths) >= 2 else 0
        orphan_penalty = 100 if len(lines) >= 2 and last_line_len <= 1 else 0
        overflow_penalty = 80 if overflow else 0
        compact_penalty = max(0, len(lines) - 1) * 6
        score = orphan_penalty + overflow_penalty + compact_penalty + balance_gap
        candidate = (score, -font_size, lines, font, measured_width)

        if best is None or candidate < best:
            best = candidate
        if not overflow and last_line_len >= 2 and len(lines) <= max_lines:
            return lines, font, measured_width

    if best is None:
        font = load_font(min_size)
        lines, measured_width = _measure_text_block(draw, text, font, max_width, max_lines)
        return lines, font, measured_width
    return best[2], best[3], best[4]


def render_landscape_theme_opening(task, frame_path, stage="covers", progress=1.0):
    width, height = 1920, 1080
    img = Image.new("RGBA", (width, height), "#08111f")
    draw = ImageDraw.Draw(img)
    title_font = load_font(54)
    headline_font = load_font(70)
    body_font = load_font(34)
    stat_font = load_font(44)
    small_font = load_font(24)

    summary = _theme_summary(task)
    reports = _theme_source_reports(task, limit=6)
    progress = _ease_in_out(progress)

    subtitle_top = height - 188
    _draw_finance_backdrop(draw, width, height, subtitle_top=subtitle_top, top_h=168, grid_region=(0, 168, width, subtitle_top))
    draw.line((0, 168, width, 168), fill=(250, 204, 21, 70), width=2)
    draw_tag(draw, 72, 46, "多研报联合背书", small_font, FINANCE_GOLD_SOFT, FINANCE_GOLD_DARK, padding_x=18, padding_y=8, radius=16)
    draw.text((72, 96), _trim_text(summary["video_title"], 32), fill="#f8fafc", font=title_font)

    if stage == "covers":
        draw.text((72, 226), "多家机构共同指向同一条主线", fill="#f8fafc", font=headline_font)
        draw.text((76, 314), summary["source_line"], fill="#8ea5c2", font=body_font)
        card_w, card_h = 420, 520
        base_y = 330
        start_x = 142
        gap = 392
        for index, report in enumerate(reports[:4]):
            reveal = max(0.0, min((progress * 4.2 - index) / 1.05, 1.0))
            x = start_x + index * gap - (1.0 - reveal) * 138
            y = base_y + (index % 2) * 24 + (1.0 - reveal) * 28
            box = (x, y, x + card_w, y + card_h)
            shadow = (x + 18, y + 18, x + card_w + 18, y + card_h + 18)
            draw_rounded_rect(draw, shadow, 28, (0, 0, 0, int(48 * reveal)))
            draw_rounded_rect(draw, box, 28, "#fbf8ee", "#e8dcc2", 2)
            preview = _load_report_pdf_preview(report, page_number=1, target_size=(int(card_w - 36), int(card_h - 86)))
            if not _paste_rounded_image(img, preview, (x + 18, y + 18, x + card_w - 18, y + card_h - 68), radius=18, background="#e2e8f0"):
                draw.text((x + 34, y + 160), "研报封面", fill="#64748b", font=body_font)
            meta = _report_source_meta(report)
            draw.text((x + 26, y + card_h - 50), _trim_text(meta["institution"], 12), fill="#0f172a", font=small_font)
        for idx, tag in enumerate(summary["tags"][:4]):
            draw_tag(draw, 76 + idx * 220, 858, tag, small_font, "#101b2e", "#fef3c7", padding_x=16, padding_y=8, radius=16, outline="#5f4b12")
    elif stage == "summary":
        panel = (96, 238, 1824, 842)
        draw_panel_shadow(draw, panel, radius=36, offset=10, fill=(0, 0, 0, 44))
        draw_rounded_rect(draw, panel, 36, PANEL_LIGHT, PANEL_LIGHT_BORDER, 1)
        draw.line((panel[0] + 56, panel[1] + 42, panel[2] - 56, panel[1] + 42), fill=(250, 204, 21, 120), width=3)
        draw.text((148, 302), "本期不是单篇观点", fill="#0f172a", font=headline_font)
        draw.text((150, 392), "而是多家机构、多份核心研报共同支撑的主题主线。", fill="#475569", font=body_font)
        stats = [
            ("参考研报", f"{summary['report_count']} 份"),
            ("覆盖机构", f"{summary['institution_count']} 家"),
            ("主题标签", " / ".join(summary["tags"][:3]) or "主题主线"),
        ]
        for index, (label, value) in enumerate(stats):
            x = 150 + index * 520
            box = (x, 522, x + 440, 704)
            draw_rounded_rect(draw, box, 28, "#fff8dc", "#f6d365", 1)
            draw.text((x + 32, 558), label, fill="#64748b", font=small_font)
            draw.text((x + 32, 604), _trim_text(value, 16), fill="#0f172a", font=stat_font)
        institutions = "、".join(summary["institutions"][:8]) or "多家机构"
        draw.text((150, 758), _trim_text(f"涉及机构：{institutions}", 56), fill="#334155", font=body_font)
    else:
        left = 116
        draw.text((left, 268), "黄金三秒", fill=FINANCE_GOLD, font=body_font)
        hook_width = 1660
        hook_font = load_font(34)
        hook_lines = wrap_text(draw, summary["opening_text"], hook_font, hook_width)
        for font_size in range(64, 30, -2):
            candidate_font = load_font(font_size)
            candidate_lines = wrap_text(draw, summary["opening_text"], candidate_font, hook_width)
            if len(candidate_lines) <= 4:
                hook_font = candidate_font
                hook_lines = candidate_lines
                break
        if len(hook_lines) > 4:
            hook_lines = wrap_text(draw, summary["opening_text"], hook_font, hook_width)[:4]
            joined = "".join(hook_lines)
            punctuation_cut = max(joined.rfind(mark) for mark in ["。", "！", "？", "；", "，"])
            if punctuation_cut > 0 and punctuation_cut > len(joined) * 0.72:
                joined = joined[: punctuation_cut + 1]
                hook_lines = wrap_text(draw, joined, hook_font, hook_width)
        hook_gap = max(58, int(getattr(hook_font, "size", 60) * 1.14))
        _layout_text_block(draw, left, 330, hook_width, hook_lines, hook_font, "#f8fafc", hook_gap)
        divider_y = min(672, 330 + len(hook_lines) * hook_gap + 34)
        draw.line((left, divider_y, 1788, divider_y), fill=(250, 204, 21, 92), width=2)
        subtitle = summary["video_subtitle"] or "接下来拆解共同逻辑、受益环节与风险分歧。"
        subtitle_lines = wrap_text(draw, subtitle, body_font, 1480, 2)
        _layout_text_block(draw, left, divider_y + 50, 1480, subtitle_lines, body_font, "#c7d5e8", 46)
        draw.text((left, 874), summary["source_line"], fill="#8ea5c2", font=body_font)

    img.convert("RGB").save(frame_path, quality=95)


def _draw_theme_storyline_panel(draw, panel, task, report, segment, transition_progress):
    left, top, right, bottom = panel
    flow = _theme_flow_items(task, report)
    focus_id = str(_segment_focus_node_id(segment) or "")
    current_index = next((idx for idx, item in enumerate(flow) if str(item["id"]) == focus_id), None)
    if current_index is None:
        current_index = max(0, min(len(flow) - 1, int(getattr(segment, "segment_index", 1) or 1) - 1))
    progress = _ease_in_out(transition_progress)

    draw.text((left + 34, top + 26), "主题主线", fill="#f8fafc", font=load_font(28))
    summary = _theme_summary(task)
    draw.text((left + 34, top + 66), _trim_text(summary["topic_name"], 26), fill="#8ea5c2", font=load_font(22))

    x_center = left + 412
    start_y = top + 148
    step_y = 78
    card_h = 58
    for idx, item in enumerate(flow):
        y = start_y + idx * step_y
        if y + card_h > bottom - 42:
            break
        is_current = idx == current_index
        is_related = abs(idx - current_index) <= 1
        alpha = 255 if is_current else (160 if is_related else 76)
        fill = "#17243a" if is_current else (12, 22, 38, 122 if is_related else 62)
        outline = FINANCE_GOLD if is_current else (80, 101, 130, 90 if is_related else 42)
        text_fill = "#fff7d6" if is_current else (202, 213, 230, alpha)
        scale = 1.0 + (0.16 * progress if is_current else 0.0)
        card_w = int((520 if is_current else 454) * scale)
        box = (x_center - card_w / 2, y - (card_h * scale - card_h) / 2, x_center + card_w / 2, y + card_h * scale)
        if idx > 0:
            prev_y = y - step_y + card_h
            is_related_line = idx == current_index or idx - 1 == current_index
            line_alpha = 170 if is_related_line else 28
            line_color = (250, 204, 21, line_alpha) if is_related_line else (96, 165, 250, line_alpha)
            draw.line((x_center, prev_y + 8, x_center, box[1] - 8), fill=line_color, width=4 if is_related_line else 2)
        if is_current:
            draw_rounded_rect(draw, (box[0] - 14, box[1] - 14, box[2] + 14, box[3] + 14), 28, (250, 204, 21, 36))
            draw_rounded_rect(draw, (box[0] - 6, box[1] - 6, box[2] + 6, box[3] + 6), 25, (250, 204, 21, 34))
        draw_rounded_rect(draw, box, 22, fill, outline, 2)
        number_box = (box[0] + 18, box[1] + 14, box[0] + 74, box[1] + 44)
        draw_rounded_rect(draw, number_box, 14, FINANCE_GOLD_SOFT if is_current else (22, 34, 52, 120 if is_related else 72))
        draw.text((number_box[0] + 14, number_box[1] + 3), f"{idx + 1:02d}", fill=FINANCE_GOLD_DARK if is_current else (147, 164, 188, alpha), font=load_font(20))
        label_lines = wrap_text(draw, item["label"], load_font(26 if is_current else 22), card_w - 114, 1)
        draw.text((box[0] + 88, box[1] + 15), label_lines[0] if label_lines else item["label"], fill=text_fill, font=load_font(26 if is_current else 22))

    current = flow[current_index] if flow else {"label": getattr(segment, "segment_title", "") or "当前章节"}
    info_box = (right - 310, bottom - 142, right - 34, bottom - 42)
    draw_rounded_rect(draw, info_box, 22, "#111827", "#5f4b12", 1)
    draw.text((info_box[0] + 20, info_box[1] + 18), "当前聚焦", fill=FINANCE_GOLD, font=load_font(20))
    focus_lines = wrap_text(draw, current["label"], load_font(26), info_box[2] - info_box[0] - 40, 2)
    _layout_text_block(draw, info_box[0] + 20, info_box[1] + 48, info_box[2] - info_box[0] - 40, focus_lines, load_font(26), "#f8fafc", 30)


def render_landscape_theme_frame(report, segment, frame_path):
    width, height = 1920, 1080
    img = Image.new("RGBA", (width, height), "#08111f")
    draw = ImageDraw.Draw(img)
    task = getattr(report, "task", None)
    summary = _theme_summary(task)
    analysis = report.analysis
    source_meta = _report_source_meta(report, segment=segment)
    source_preview = _load_report_pdf_preview(report, page_number=source_meta["source_page"], target_size=(260, 170))
    transition_progress = getattr(segment, "transition_progress", 1.0)

    top_h = 178
    bottom_h = 188
    content_top = top_h + 28
    content_bottom = height - bottom_h - 34
    left_panel = (56, content_top, 1098, content_bottom)
    right_panel = (1128, content_top, 1864, content_bottom)
    subtitle_top = height - bottom_h

    _draw_finance_backdrop(
        draw,
        width,
        height,
        subtitle_top=subtitle_top,
        top_h=top_h,
        grid_region=(0, top_h, width, subtitle_top),
    )
    draw.rectangle((0, subtitle_top - 16, width, subtitle_top + 18), fill=(5, 10, 20, 122))
    draw.line((0, top_h, width, top_h), fill=(250, 204, 21, 62), width=2)
    draw.line((0, subtitle_top, width, subtitle_top), fill=(250, 204, 21, 50), width=2)

    draw_tag(draw, 56, 18, f"研报解读｜{_trim_text(summary['topic_name'], 10)}", load_font(22), FINANCE_GOLD_SOFT, FINANCE_GOLD_DARK, padding_x=16, padding_y=7, radius=14)
    title_lines, title_font, _ = _adaptive_text_layout(draw, summary["video_title"], 42, 30, 1340, 1)
    _layout_text_block(draw, 56, 56, 1340, title_lines, title_font, "#f8fafc", 44)
    sub_lines, sub_font, _ = _adaptive_text_layout(draw, summary["video_subtitle"], 22, 18, 1340, 1)
    _layout_text_block(draw, 56, 108, 1340, sub_lines, sub_font, "#8ea5c2", 28)
    draw_rounded_rect(draw, (56, 140, 1514, 168), 14, "#101b2e", "#5f4b12", 1)
    draw.text((72, 145), _trim_text(summary["source_line"], 76), fill="#c7d5e8", font=load_font(22))

    draw_panel_shadow(draw, left_panel, radius=28, offset=6, fill=(0, 0, 0, 32))
    draw_panel_shadow(draw, right_panel, radius=28, offset=6, fill=(0, 0, 0, 28))
    draw_rounded_rect(draw, left_panel, 28, PANEL_DARK, "#1d2a40", 2)
    draw_rounded_rect(draw, right_panel, 28, PANEL_LIGHT, PANEL_LIGHT_BORDER, 1)

    _draw_theme_storyline_panel(draw, left_panel, task, report, segment, transition_progress)

    preview_box = (right_panel[2] - 250, right_panel[1] + 26, right_panel[2] - 32, right_panel[1] + 166)
    draw_rounded_rect(draw, preview_box, 18, "#fff8dc", "#ead28a", 1)
    if not _paste_rounded_image(img, source_preview, preview_box, radius=18, background="#dbe4f0"):
        draw.text((preview_box[0] + 34, preview_box[1] + 46), "来源页", fill="#475569", font=load_font(28))
    draw_tag(draw, preview_box[0] + 12, preview_box[3] - 38, source_meta["page_label"], load_font(20), "#111827", "#fef3c7", padding_x=12, padding_y=5, radius=12)

    draw.text((right_panel[0] + 32, right_panel[1] + 30), "本段解读", fill="#0f172a", font=load_font(28))
    draw_tag(draw, right_panel[0] + 32, right_panel[1] + 74, "主题级主线", load_font(22), FINANCE_GOLD_SOFT, FINANCE_GOLD_DARK, padding_x=16, padding_y=7, radius=14)
    title_width = preview_box[0] - right_panel[0] - 58
    segment_title = _segment_display_title(segment, analysis=analysis, max_chars=28)
    title_lines = wrap_text(draw, segment_title, load_font(46), title_width, 2)
    _layout_text_block(draw, right_panel[0] + 32, right_panel[1] + 126, title_width, title_lines, load_font(46), "#0f172a", 54)

    detail_y = right_panel[1] + 142 + len(title_lines) * 54
    draw.text((right_panel[0] + 32, detail_y), "详细说明", fill="#475569", font=load_font(28))
    detail_text = _segment_detail_text(segment, analysis=analysis, min_chars=62, max_chars=108)
    detail_font = load_font(28)
    detail_line_gap = 38
    points_font = load_font(30)
    points_line_gap = 58
    card_safe_bottom = right_panel[3] - 38
    for max_detail_lines in (4, 3, 2):
        detail_lines = wrap_text(draw, detail_text, detail_font, right_panel[2] - right_panel[0] - 64, max_detail_lines)
        points_y = detail_y + 48 + len(detail_lines) * detail_line_gap + 22
        points_start_y = points_y + 50
        fit_count = _fit_points_count(points_start_y, card_safe_bottom, preferred_count=3, line_gap=points_line_gap, min_count=1)
        if fit_count >= 2 or max_detail_lines == 2:
            break
    _layout_text_block(draw, right_panel[0] + 32, detail_y + 48, right_panel[2] - right_panel[0] - 64, detail_lines, detail_font, "#1e293b", detail_line_gap)

    draw.text((right_panel[0] + 32, points_y), "核心要点", fill="#475569", font=load_font(28))
    points = _segment_points(segment, analysis=analysis, max_points=3)[:fit_count]
    _draw_bullet_list(draw, right_panel[0] + 36, points_y + 50, points, points_font, points_line_gap, text_fill="#1e293b", bullet_fill=FINANCE_GOLD)

    draw.line((68, subtitle_top + 28, width - 68, subtitle_top + 28), fill=(250, 204, 21, 68), width=1)
    img.convert("RGB").save(frame_path, quality=95)


def _build_segment_visual_states(report, segment, duration):
    duration = max(float(duration or 0.0), 0.05)
    nodes = node_map(getattr(report, "mindmap", None))
    focus_node = nodes.get(str(_segment_focus_node_id(segment) or "")) or {}
    children = _node_children(nodes, focus_node)[:3]
    transition_duration = 0.0
    states = []

    if focus_node and duration >= 4.2:
        transition_duration = min(max(duration * 0.14, 0.72), 0.96)
        transition_steps = [0.0, 0.14, 0.32, 0.52, 0.74, 1.0]
        step_duration = transition_duration / len(transition_steps)
        for progress in transition_steps:
            states.append({
                "child_focus_id": None,
                "duration": round(step_duration, 3),
                "transition_progress": progress,
            })

    stable_duration = max(duration - transition_duration, 0.05)
    if not children or stable_duration < 8.5:
        states.append({
            "child_focus_id": None,
            "duration": stable_duration,
            "transition_progress": 1.0,
        })
    else:
        base_duration = min(max(stable_duration * 0.28, 2.4), max(stable_duration - len(children) * 1.1, 2.2))
        remaining = max(stable_duration - base_duration, 0.9)
        child_duration = remaining / len(children)
        states.append({
            "child_focus_id": None,
            "duration": round(base_duration, 3),
            "transition_progress": 1.0,
        })
        for child in children:
            states.append({
                "child_focus_id": str(child.get("id")),
                "duration": round(child_duration, 3),
                "transition_progress": 1.0,
            })

    normalized = []
    total = sum(item["duration"] for item in states)
    factor = duration / total if total > 0 else 1.0
    for item in states:
        normalized.append({
            "child_focus_id": item["child_focus_id"],
            "duration": round(item["duration"] * factor, 3),
            "transition_progress": item.get("transition_progress", 1.0),
        })
    return normalized


def render_landscape_frame(report, segment, frame_path):
    if _is_theme_segment(report, segment):
        return render_landscape_theme_frame(report, segment, frame_path)

    width, height = 1920, 1080
    img = Image.new("RGBA", (width, height), "#08111f")
    draw = ImageDraw.Draw(img)
    title_font = load_font(40)
    subtitle_font = load_font(22)
    panel_title_font = load_font(28)
    node_font = load_font(34)
    focus_font = load_font(46)
    bullet_font = load_font(34)
    detail_font = load_font(28)
    small_font = load_font(22)
    chip_font = load_font(24)

    analysis = report.analysis
    mindmap = report.mindmap
    nodes = node_map(mindmap)
    root = nodes.get("root") or next(iter(nodes.values()), {"label": analysis.report_title if analysis else report.file_name})
    source_meta = _report_source_meta(report, segment=segment)
    source_preview = _load_report_pdf_preview(report, page_number=source_meta["source_page"], target_size=(360, 240))
    focus_id = _segment_focus_node_id(segment)
    focus = nodes.get(str(focus_id or "")) or {}
    focus_children = _focus_children_for_render(nodes, root, focus)
    segment_title = _segment_display_title(segment, analysis=analysis, max_chars=28)
    root_label = _root_display_label(report, root, segment=segment, max_chars=28)
    focus_label = _node_display_label(focus, fallback=segment_title, max_chars=24)
    child_fallback_labels = _meaningful_child_labels(focus_children)
    segment_points = _segment_visual_points(
        segment,
        analysis=analysis,
        max_points=3,
        fallback_labels=child_fallback_labels,
    )
    visual_child_focus_id = getattr(segment, "visual_child_focus_id", None)
    transition_progress = _ease_in_out(getattr(segment, "transition_progress", 1.0))
    previous_focus_node_id = getattr(segment, "previous_focus_node_id", None)

    top_h = 188
    bottom_h = 198
    content_top = top_h + 24
    content_bottom = height - bottom_h - 34
    left_panel = (56, content_top, 1154, content_bottom)
    right_panel = (1184, content_top, 1864, content_bottom)
    subtitle_top = height - bottom_h

    draw.rectangle((0, 0, width, height), fill="#08111f")
    draw.rectangle((0, 0, width, top_h), fill="#0d1728")
    draw.rectangle((0, subtitle_top, width, height), fill="#060d18")
    draw.rectangle((0, subtitle_top - 18, width, subtitle_top + 24), fill=(8, 17, 31, 160))

    draw.line((0, top_h, width, top_h), fill=(96, 165, 250, 60), width=2)
    draw.line((0, subtitle_top, width, subtitle_top), fill=(96, 165, 250, 60), width=2)

    main_title, sub_title = _title_parts(analysis.report_title if analysis else report.file_name, analysis=analysis, segment=segment)
    header_text_x = 56
    header_block_x = 40
    header_right = 1864
    header_text_width = header_right - header_text_x - 16
    top_label = f"研报解读｜{_trim_text(source_meta['institution'] if source_meta['institution'] != '机构研报' else ((analysis.theme if analysis else '') or '当前主题'), 10)}"
    draw_tag(draw, header_block_x, 22, top_label, small_font, "#0f172a", "#dbeafe", padding_x=16, padding_y=7, radius=14)

    header_lines, header_font, _ = _adaptive_text_layout(draw, main_title, 40, 30, header_text_width, 2)
    header_top = 52
    header_gap = max(40, int(getattr(header_font, "size", 40) * 1.08))
    _layout_text_block(draw, header_text_x, header_top, header_text_width, header_lines, header_font, "#f8fafc", header_gap)

    subtitle_text = _trim_text(sub_title or source_meta["core_view"], 42)
    subtitle_lines, subtitle_render_font, _ = _adaptive_text_layout(draw, subtitle_text, 22, 18, header_text_width, 1)
    subtitle_top = header_top + len(header_lines) * header_gap + 8
    _layout_text_block(draw, header_text_x, subtitle_top, header_text_width, subtitle_lines, subtitle_render_font, "#8ea5c2", max(24, int(getattr(subtitle_render_font, "size", 22) * 1.08)))

    source_bar_top = subtitle_top + max(28, int(getattr(subtitle_render_font, "size", 22) * 1.18)) + 10
    draw_rounded_rect(draw, (header_block_x, source_bar_top, header_right, source_bar_top + 28), 14, "#101b2e", "#1f3654", 1)
    draw.text((header_text_x, source_bar_top + 5), _trim_text(source_meta["source_line"], 72), fill="#c7d5e8", font=small_font)

    draw_panel_shadow(draw, left_panel, radius=28, offset=6, fill=(0, 0, 0, 32))
    draw_panel_shadow(draw, right_panel, radius=28, offset=6, fill=(0, 0, 0, 28))
    draw_rounded_rect(draw, left_panel, 28, "#0d1728", "#1d2a40", 2)
    draw_rounded_rect(draw, right_panel, 28, "#f8fbff", "#d9e4f2", 1)
    _draw_region_grid(draw, (left_panel[0] + 18, left_panel[1] + 18, left_panel[2] - 18, left_panel[3] - 18), step=120, color=(96, 165, 250, 2))

    draw.text((left_panel[0] + 32, left_panel[1] + 24), "思维导图聚焦", fill="#e2e8f0", font=panel_title_font)
    draw.text((right_panel[0] + 32, right_panel[1] + 24), "本段解读", fill="#0f172a", font=panel_title_font)

    preview_box = (right_panel[2] - 232, right_panel[1] + 26, right_panel[2] - 32, right_panel[1] + 154)
    draw_rounded_rect(draw, preview_box, 18, "#eef4fb", "#d7e2ef", 1)
    if _paste_rounded_image(img, source_preview, preview_box, radius=18, background="#dbe4f0"):
        pass
    else:
        draw.text((preview_box[0] + 28, preview_box[1] + 42), "来源页", fill="#475569", font=panel_title_font)
        draw.text((preview_box[0] + 28, preview_box[1] + 84), source_meta["page_label"], fill="#64748b", font=small_font)
    draw_tag(
        draw,
        preview_box[0] + 12,
        preview_box[3] - 38,
        source_meta["page_label"],
        small_font,
        "#08111f",
        "#e2e8f0",
        padding_x=12,
        padding_y=5,
        radius=12,
    )

    root_center_x = (left_panel[0] + left_panel[2]) / 2
    root_box = (root_center_x - 164, left_panel[1] + 84, root_center_x + 164, left_panel[1] + 154)
    draw_rounded_rect(draw, root_box, 22, "#111c2f", "#334155", 2)
    root_lines, root_font, _ = _adaptive_text_layout(draw, root_label, 34, 26, 290, 2)
    draw_centered_text(draw, root_center_x, left_panel[1] + 105, root_lines, root_font, "#f8fafc", 34)

    focus_base_center_x = root_center_x
    focus_base_center_y = left_panel[1] + 292
    child_y = left_panel[3] - 148
    source_anchor_x, source_anchor_y = _branch_anchor_position(
        nodes,
        root,
        previous_focus_node_id or focus_id,
        root_center_x,
        child_y,
    )
    focus_center_x = source_anchor_x + (focus_base_center_x - source_anchor_x) * transition_progress
    focus_center_y = source_anchor_y + (focus_base_center_y - source_anchor_y) * transition_progress
    focus_scale = 1.02 + (1.16 - 1.02) * transition_progress
    focus_w = int(324 * focus_scale)
    focus_h = int(106 * focus_scale)
    focus_box = (
        focus_center_x - focus_w / 2,
        focus_center_y - focus_h / 2,
        focus_center_x + focus_w / 2,
        focus_center_y + focus_h / 2,
    )
    line_alpha = int(90 + 165 * transition_progress)
    glow_alpha_outer = int(18 + 38 * transition_progress)
    glow_alpha_inner = int(12 + 30 * transition_progress)
    draw.line((root_center_x, root_box[3], focus_center_x, focus_box[1] - 12), fill=(96, 165, 250, line_alpha), width=7)
    draw_rounded_rect(draw, (focus_box[0] - 12, focus_box[1] - 12, focus_box[2] + 12, focus_box[3] + 12), 30, (37, 99, 235, glow_alpha_outer))
    draw_rounded_rect(draw, (focus_box[0] - 5, focus_box[1] - 5, focus_box[2] + 5, focus_box[3] + 5), 28, (96, 165, 250, glow_alpha_inner))
    draw_rounded_rect(draw, focus_box, 26, "#2563eb", "#bfdbfe", 3)
    focus_lines, focus_render_font, _ = _adaptive_text_layout(draw, focus_label, 46, 32, focus_w - 70, 2)
    focus_top = focus_box[1] + (18 if len(focus_lines) <= 1 else 10)
    focus_gap = max(34, int(getattr(focus_render_font, "size", 46) * 0.92))
    draw_centered_text(draw, focus_center_x, focus_top, focus_lines, focus_render_font, "#ffffff", focus_gap)

    child_count = max(len(focus_children), 1)
    max_child_width = 232
    child_cards = []
    for idx, child in enumerate(focus_children):
        child_label = _meaningful_label(child.get("label") or child.get("id"), 24)
        if not child_label and idx < len(segment_points):
            child_label = segment_points[idx]
        if not child_label:
            continue
        child_lines, child_font, measured_width = _adaptive_text_layout(draw, child_label, 24, 20, max_child_width, 2)
        card_width = min(max(int(measured_width + 66), 218), 250)
        child_cards.append({
            "node": child,
            "lines": child_lines,
            "font": child_font,
            "width": card_width,
        })

    if child_cards:
        gap = 28 if child_count >= 3 else 44
        total_width = sum(item["width"] for item in child_cards) + gap * (len(child_cards) - 1)
        start_x = root_center_x - total_width / 2
        min_x = left_panel[0] + 92
        max_x = left_panel[2] - 92 - total_width
        start_x = max(min_x, min(start_x, max_x))
        cursor_x = start_x
        child_positions = []
        for item in child_cards:
            center = cursor_x + item["width"] / 2
            child_positions.append(center)
            cursor_x += item["width"] + gap
    else:
        child_positions = [root_center_x]

    branch_hub_y = focus_box[3] + 42
    branch_line_y = child_y - 58
    if child_cards:
        branch_left = child_positions[0]
        branch_right = child_positions[-1]
        branch_color = (96, 165, 250, int(48 + 112 * transition_progress))
        draw.line((focus_center_x, focus_box[3], focus_center_x, branch_hub_y), fill=branch_color, width=5)
        draw.line((focus_center_x, branch_hub_y, focus_center_x, branch_line_y), fill=branch_color, width=4)
        if len(child_positions) > 1:
            draw.line((branch_left, branch_line_y, branch_right, branch_line_y), fill=(71, 124, 209, int(36 + 86 * transition_progress)), width=3)

    for idx, item in enumerate(child_cards):
        child = item["node"]
        child_x = child_positions[idx]
        is_child_active = str(child.get("id")) == str(visual_child_focus_id)
        reveal_progress = max(0.0, min((transition_progress - idx * 0.13) / 0.55, 1.0))
        inactive_alpha = int(42 + 88 * reveal_progress)
        active_alpha = int(110 + 115 * reveal_progress)
        child_fill = (15, 23, 42, inactive_alpha) if not is_child_active else (18, 58, 122, active_alpha)
        child_outline = (51, 65, 85, int(38 + 48 * reveal_progress)) if not is_child_active else (147, 197, 253, int(120 + 80 * reveal_progress))
        child_text = (196, 206, 223, int(92 + 96 * reveal_progress)) if not is_child_active else (239, 246, 255, int(120 + 110 * reveal_progress))
        child_line = (51, 65, 85, int(26 + 50 * reveal_progress)) if not is_child_active else (96, 165, 250, int(110 + 90 * reveal_progress))
        box = (child_x - item["width"] / 2, child_y - 38, child_x + item["width"] / 2, child_y + 38)
        draw.line((child_x, branch_line_y, child_x, box[1] - 10), fill=child_line, width=4 if is_child_active else 2)
        if is_child_active:
            draw_rounded_rect(draw, (box[0] - 5, box[1] - 5, box[2] + 5, box[3] + 5), 22, (59, 130, 246, 34))
        draw_rounded_rect(draw, box, 18, child_fill, child_outline, 2)
        child_gap = max(22, int(getattr(item["font"], "size", 24) * 0.95))
        child_top = child_y - (14 if len(item["lines"]) <= 1 else 26)
        draw_centered_text(draw, child_x, child_top, item["lines"], item["font"], child_text, child_gap)

    points = segment_points
    detail_text = _segment_detail_text(segment, analysis=analysis, min_chars=60, max_chars=96)

    current_chip_text = "当前节点"
    if visual_child_focus_id:
        active_child = nodes.get(str(visual_child_focus_id))
        if active_child:
            active_label = _node_display_label(active_child, fallback=segment_title, max_chars=12)
            current_chip_text = f"子节点：{active_label}"
    draw_tag(draw, right_panel[0] + 32, right_panel[1] + 70, current_chip_text, small_font, "#eff6ff", "#1d4ed8", padding_x=16, padding_y=7, radius=14)
    title_width = preview_box[0] - right_panel[0] - 56
    title_lines = wrap_text(draw, segment_title, focus_font, title_width, 2)
    _layout_text_block(draw, right_panel[0] + 32, right_panel[1] + 126, title_width, title_lines, focus_font, "#0f172a", 52)
    detail_title_y = right_panel[1] + 150 + len(title_lines) * 52
    draw.text((right_panel[0] + 32, detail_title_y), "详细说明", fill="#475569", font=panel_title_font)
    detail_lines = wrap_text(draw, detail_text, detail_font, right_panel[2] - right_panel[0] - 64, 4)
    _layout_text_block(draw, right_panel[0] + 32, detail_title_y + 48, right_panel[2] - right_panel[0] - 64, detail_lines, detail_font, "#1e293b", 38)
    points_title_y = detail_title_y + 48 + len(detail_lines) * 38 + 22
    points = points[:2] if len(detail_lines) >= 4 else points[:3]
    draw.text((right_panel[0] + 32, points_title_y), "核心要点", fill="#475569", font=panel_title_font)
    _draw_bullet_list(draw, right_panel[0] + 36, points_title_y + 52, points, bullet_font, 76, text_fill="#1e293b", bullet_fill="#2563eb")
    draw.line((68, subtitle_top + 30, width - 68, subtitle_top + 30), fill=(148, 163, 184, 70), width=1)

    img.convert("RGB").save(frame_path, quality=95)


def render_landscape_source_card(report, segment, frame_path, reveal_progress=1.0):
    width, height = 1920, 1080
    img = Image.new("RGBA", (width, height), "#08111f")
    draw = ImageDraw.Draw(img)
    title_font = load_font(46)
    subtitle_font = load_font(26)
    body_font = load_font(32)
    small_font = load_font(24)
    panel_title_font = load_font(30)

    source_meta = _report_source_meta(report, segment=segment)
    preview = _load_report_pdf_preview(report, page_number=1, target_size=(860, 760))
    analysis = getattr(report, "analysis", None)
    reveal_progress = _ease_in_out(reveal_progress)

    subtitle_top = 882
    draw.rectangle((0, 0, width, height), fill="#08111f")
    draw.rectangle((0, subtitle_top, width, height), fill="#060d18")
    draw.line((0, subtitle_top, width, subtitle_top), fill=(96, 165, 250, 64), width=2)
    _draw_region_grid(draw, (0, 0, width, subtitle_top), step=132, color=(96, 165, 250, 4))

    preview_panel = (92, 114, 884, 826)
    meta_panel = (934, 114, 1828, 826)
    draw_panel_shadow(draw, preview_panel, radius=32, offset=8, fill=(0, 0, 0, 42))
    draw_panel_shadow(draw, meta_panel, radius=32, offset=8, fill=(0, 0, 0, 38))
    draw_rounded_rect(draw, preview_panel, 32, "#0f172a", "#20314a", 2)
    draw_rounded_rect(draw, meta_panel, 32, "#f8fbff", "#d9e4f2", 1)
    draw.text((126, 72), "来源研报", fill="#dbeafe", font=panel_title_font)

    preview_image_box = (preview_panel[0] + 28, preview_panel[1] + 32, preview_panel[2] - 28, preview_panel[3] - 32)
    if not _paste_rounded_image(img, preview, preview_image_box, radius=24, background="#dbe4f0"):
        draw.text((preview_panel[0] + 64, preview_panel[1] + 280), "研报封面预览", fill="#64748b", font=title_font)
    if reveal_progress < 1.0:
        overlay_alpha = int((1.0 - reveal_progress) * 140)
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        overlay_draw.rounded_rectangle(preview_image_box, radius=24, fill=(8, 17, 31, overlay_alpha))
        img.alpha_composite(overlay)
    draw_tag(draw, preview_panel[0] + 36, preview_panel[3] - 72, "封面页", small_font, "#08111f", "#e2e8f0", padding_x=14, padding_y=6, radius=14)
    stage_two = reveal_progress >= 0.24
    stage_three = reveal_progress >= 0.48
    stage_four = reveal_progress >= 0.72

    if stage_two:
        draw_tag(draw, meta_panel[0] + 34, meta_panel[1] + 34, source_meta["institution"], small_font, "#eff6ff", "#1d4ed8", padding_x=16, padding_y=7, radius=14)
        if source_meta["theme"]:
            draw_tag(draw, meta_panel[0] + 220, meta_panel[1] + 34, _trim_text(source_meta["theme"], 10), small_font, "#f8fafc", "#475569", padding_x=14, padding_y=7, radius=14, outline="#d9e4f2")

        title_lines = wrap_text(draw, source_meta["title"], title_font, meta_panel[2] - meta_panel[0] - 68, 3)
        _layout_text_block(draw, meta_panel[0] + 34, meta_panel[1] + 106, meta_panel[2] - meta_panel[0] - 68, title_lines, title_font, "#0f172a", 56)
    else:
        title_lines = []

    info_y = meta_panel[1] + 126 + len(title_lines) * 56
    if stage_three and source_meta["date"]:
        draw.text((meta_panel[0] + 34, info_y), source_meta["date"], fill="#64748b", font=subtitle_font)
        info_y += 50

    if stage_four:
        draw.text((meta_panel[0] + 34, info_y), "一句核心结论", fill="#475569", font=panel_title_font)
        core_lines = wrap_text(draw, source_meta["core_view"], body_font, meta_panel[2] - meta_panel[0] - 68, 4)
        _layout_text_block(draw, meta_panel[0] + 34, info_y + 52, meta_panel[2] - meta_panel[0] - 68, core_lines, body_font, "#1e293b", 42)

        footer_y = meta_panel[3] - 136
        draw.text((meta_panel[0] + 34, footer_y), _trim_text(source_meta["source_line"], 58), fill="#0f172a", font=subtitle_font)
        draw.text((meta_panel[0] + 34, footer_y + 48), "接下来进入本篇研报解读", fill="#64748b", font=small_font)

    draw.line((68, subtitle_top + 30, width - 68, subtitle_top + 30), fill=(148, 163, 184, 70), width=1)
    img.convert("RGB").save(frame_path, quality=95)


def render_portrait_frame(report, segment, frame_path):
    width, height = 1080, 1920
    img = Image.new("RGBA", (width, height), "#070b14")
    draw = ImageDraw.Draw(img)
    body_font = load_font(32)
    insight_title_font = load_font(42)
    small_font = load_font(23)
    tiny_font = load_font(20)

    analysis = report.analysis
    mindmap = report.mindmap
    nodes = node_map(mindmap)
    root = nodes.get("root") or next(iter(nodes.values()), {"label": analysis.report_title if analysis else report.file_name})
    focus_id = _segment_focus_node_id(segment)
    focus = nodes.get(str(focus_id or "")) or {"label": segment.segment_title}
    focus_children = _focus_children_for_render(nodes, root, focus)
    segment_title = _segment_display_title(segment, analysis=analysis, max_chars=28)
    root_label = _root_display_label(report, root, segment=segment, max_chars=26)
    focus_label = _node_display_label(focus, fallback=segment_title, max_chars=24)
    child_fallback_labels = _meaningful_child_labels(focus_children)
    points = _segment_visual_points(
        segment,
        analysis=analysis,
        max_points=3,
        fallback_labels=child_fallback_labels,
    )
    source_meta = _report_source_meta(report, segment=segment)
    source_preview = _load_portrait_source_preview(report, segment, page_number=source_meta["source_page"])
    subtitle_top = 1588

    _draw_portrait_editorial_backdrop(img, draw, width, height, subtitle_top, source_image=source_preview)
    theme_label = _trim_text((analysis.theme if analysis else source_meta["theme"]) or "研报解读", 22)
    segment_no = _safe_int(getattr(segment, "segment_index", None), 0) or 0

    content_left = 60
    content_right = width - 60
    content_w = content_right - content_left
    brand_y = 600
    badge_size = 56
    draw.ellipse(
        (content_left, brand_y, content_left + badge_size, brand_y + badge_size),
        fill="#d11b1b",
        outline=(255, 218, 213, 92),
        width=2,
    )
    draw.rectangle((content_left + 18, brand_y + 18, content_left + 38, brand_y + 38), fill=PORTRAIT_PRIMARY_SOFT)
    institution = source_meta["institution"]
    draw.text((content_left + 72, brand_y + 6), _trim_text(institution, 18), fill=PORTRAIT_PRIMARY_SOFT, font=small_font)
    context_text = " · ".join(item for item in [theme_label, source_meta["date"], source_meta["page_label"]] if item)
    if context_text:
        draw.text((content_left + 72, brand_y + 36), _trim_text(context_text, 30), fill="#aeb8c8", font=tiny_font)
    if segment_no:
        frame_label = f"FRAME {segment_no:02d}"
        frame_bbox = draw.textbbox((0, 0), frame_label, font=tiny_font)
        draw.text((content_right - (frame_bbox[2] - frame_bbox[0]), brand_y + 12), frame_label, fill="#b6c2d4", font=tiny_font)

    main_title, accent_title = _portrait_title_parts(report, source_meta)
    title_y = brand_y + 104
    if accent_title:
        main_lines, main_font, _ = _adaptive_text_layout(draw, main_title, 62, 48, content_w, 1)
        main_gap = max(62, int(getattr(main_font, "size", 62) * 1.08))
        for idx, line in enumerate(main_lines):
            _draw_text_with_shadow(draw, (content_left, title_y + idx * main_gap), line, main_font, "#f8fafc")
        accent_y = title_y + len(main_lines) * main_gap + 2
        accent_lines, accent_font, _ = _adaptive_text_layout(draw, accent_title, 64, 46, content_w, 2)
        accent_gap = max(62, int(getattr(accent_font, "size", 64) * 1.08))
        for idx, line in enumerate(accent_lines):
            _draw_text_with_shadow(draw, (content_left, accent_y + idx * accent_gap), line, accent_font, PORTRAIT_PRIMARY)
        title_bottom = accent_y + len(accent_lines) * accent_gap
    else:
        title_lines, title_font, _ = _adaptive_text_layout(draw, main_title, 64, 44, content_w, 2)
        title_gap = max(62, int(getattr(title_font, "size", 64) * 1.08))
        for idx, line in enumerate(title_lines):
            _draw_text_with_shadow(draw, (content_left, title_y + idx * title_gap), line, title_font, "#f8fafc")
        title_bottom = title_y + len(title_lines) * title_gap

    subheading = _subheading_text(analysis, segment) or source_meta["core_view"]
    sub_lines, sub_font, _ = _adaptive_text_layout(draw, subheading, 27, 22, content_w, 2)
    subtitle_y = title_bottom + 26
    _layout_text_block(draw, content_left, subtitle_y, content_w, sub_lines, sub_font, "#b7c3d5", 34)

    insight_top = min(max(940, subtitle_y + len(sub_lines) * 34 + 48), subtitle_top - 590)
    insight_bottom = subtitle_top - 76
    insight_box = (content_left, insight_top, content_right, insight_bottom)
    draw_panel_shadow(draw, insight_box, radius=34, offset=8, fill=(0, 0, 0, 46))
    draw_rounded_rect(draw, insight_box, 34, PORTRAIT_GLASS, PORTRAIT_GLASS_BORDER, 1)
    draw.text((content_left + 38, insight_top + 34), "核心洞察", fill=PORTRAIT_PRIMARY, font=insight_title_font)
    focus_lines, focus_font, _ = _adaptive_text_layout(draw, focus_label, 34, 27, content_w - 76, 1)
    _layout_text_block(
        draw,
        content_left + 38,
        insight_top + 90,
        content_w - 76,
        focus_lines,
        focus_font,
        "#f8fafc",
        38,
    )
    row_top = insight_top + 148
    point_count = 3 if (insight_bottom - row_top) >= 350 else 2
    _draw_portrait_insight_rows(
        draw,
        content_left + 38,
        row_top,
        content_right - 38,
        points[:point_count],
        body_font,
        small_font,
    )

    footer_y = insight_bottom - 54
    draw.line((content_left + 38, footer_y, content_right - 38, footer_y), fill=(255, 255, 255, 24), width=1)
    brand_label = _portrait_brand_label(report)
    draw.text((content_left + 38, footer_y + 18), brand_label, fill="#bdf4ff", font=small_font)
    source_tail = _trim_text(f"{root_label} -> {segment_title}", 32)
    tail_bbox = draw.textbbox((0, 0), source_tail, font=tiny_font)
    draw.text((content_right - 38 - (tail_bbox[2] - tail_bbox[0]), footer_y + 21), source_tail, fill="#8fa4c2", font=tiny_font)

    img.convert("RGB").save(frame_path, quality=95)


def escape_subtitle_filter_path(path):
    return str(Path(path).resolve()).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def make_concat_video(
    frame_items,
    output_path,
    width,
    height,
    audio_path=None,
    subtitle_path=None,
    transition_duration=0.6,
    motion=False,
):
    output_path = Path(output_path)
    cmd = ["ffmpeg", "-y"]
    transition_duration = max(0.0, min(float(transition_duration or 0.0), 1.2))
    visible_duration = sum(max(float(duration), 1.0) for _, duration in frame_items)
    frame_item_limit = max(MAX_XFADE_FRAME_ITEMS, 96) if motion else MAX_XFADE_FRAME_ITEMS
    use_xfade = (
        len(frame_items) > 1
        and transition_duration > 0.0
        and len(frame_items) <= frame_item_limit
        and visible_duration <= MAX_XFADE_DURATION_SEC
    )
    post_trim_duration = 0.0
    encode_path = output_path
    video_extensions = {".mp4", ".mov", ".m4v", ".webm", ".mkv"}
    video_inputs = [
        Path(frame_path).suffix.lower() in video_extensions
        for frame_path, _ in frame_items
    ]

    if use_xfade:
        for index, (frame_path, duration) in enumerate(frame_items):
            segment_duration = max(float(duration), 1.0)
            input_duration = segment_duration + transition_duration if index < len(frame_items) - 1 else segment_duration
            if video_inputs[index]:
                cmd.extend([
                    "-t",
                    f"{input_duration:.3f}",
                    "-i",
                    str(Path(frame_path).resolve()),
                ])
                continue
            if motion:
                cmd.extend(["-framerate", "30"])
            cmd.extend([
                "-loop",
                "1",
                "-t",
                f"{input_duration:.3f}",
                "-i",
                str(Path(frame_path).resolve()),
            ])
    else:
        concat_path = output_path.with_suffix(".frames.txt")
        lines = []
        for frame_path, duration in frame_items:
            safe_frame = str(Path(frame_path).resolve()).replace("'", "'\\''")
            lines.append(f"file '{safe_frame}'")
            lines.append(f"duration {max(float(duration), 0.05):.3f}")
        if frame_items:
            safe_frame = str(Path(frame_items[-1][0]).resolve()).replace("'", "'\\''")
            lines.append(f"file '{safe_frame}'")
        concat_path.write_text("\n".join(lines), encoding="utf-8")
        cmd.extend([
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
        ])

    if audio_path:
        cmd.extend(["-i", str(audio_path)])

    if use_xfade:
        filter_parts = []
        for index in range(len(frame_items)):
            media_prefix = (
                f"[{index}:v]tpad=stop_mode=clone:stop_duration={transition_duration:.3f},"
                if video_inputs[index]
                else f"[{index}:v]"
            )
            base_filter = (
                f"{media_prefix}scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,"
            )
            if motion and not video_inputs[index]:
                base_filter += (
                    f"zoompan=z='min(1.0+on*0.00010,1.022)':"
                    f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                    f"d=1:s={width}x{height}:fps=30,"
                )
            filter_parts.append(
                f"{base_filter}fps=30,format=yuv420p,setsar=1[v{index}]"
            )

        visible_total = max(float(frame_items[0][1]), 1.0)
        current_label = "v0"
        for index in range(1, len(frame_items)):
            output_label = f"vx{index}"
            offset = max(0.0, visible_total - transition_duration)
            filter_parts.append(
                f"[{current_label}][v{index}]xfade=transition=fade:duration={transition_duration:.3f}:offset={offset:.3f}[{output_label}]"
            )
            current_label = output_label
            visible_total += max(float(frame_items[index][1]), 1.0)

        final_label = current_label
        if subtitle_path:
            subtitle = escape_subtitle_filter_path(subtitle_path)
            filter_parts.append(
                f"[{final_label}]subtitles='{subtitle}':"
                "force_style='FontName=Arial Unicode MS,FontSize=14,"
                "PrimaryColour=&H00F8FAFC,OutlineColour=&H00131B29,BorderStyle=1,Outline=1,Shadow=0,Alignment=2,MarginV=18'"
                "[vfinal]"
            )
            final_label = "vfinal"

        cmd.extend([
            "-filter_complex",
            ";".join(filter_parts),
            "-map",
            f"[{final_label}]",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
        ])
    else:
        video_filter = f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
        if subtitle_path:
            subtitle = escape_subtitle_filter_path(subtitle_path)
            video_filter += (
                f",subtitles='{subtitle}':"
                "force_style='FontName=Arial Unicode MS,FontSize=14,"
                "PrimaryColour=&H00F8FAFC,OutlineColour=&H00131B29,BorderStyle=1,Outline=1,Shadow=0,Alignment=2,MarginV=18'"
            )
        cmd.extend([
            "-vf",
            video_filter,
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
        ])

    if audio_path:
        if not use_xfade:
            cmd.extend(["-map", "0:v"])
        audio_input_index = len(frame_items) if use_xfade else 1
        audio_limit = media_duration(audio_path)
        cmd.extend(["-map", f"{audio_input_index}:a", "-c:a", "aac"])
        if audio_limit > 0:
            post_trim_duration = audio_limit
            encode_path = output_path.with_name(f"{output_path.stem}.uncut{output_path.suffix}")
        else:
            cmd.extend(["-shortest"])
    else:
        cmd.extend(["-an"])
    cmd.append(str(encode_path))

    timeout_scale = 3.2 if motion and use_xfade else (1.2 if use_xfade else 0.35)
    timeout_padding = 300 if motion and use_xfade else 180
    render_timeout = max(
        240,
        int((post_trim_duration or visible_duration or 0) * timeout_scale)
        + timeout_padding,
    )
    subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=render_timeout)
    if post_trim_duration > 0 and encode_path != output_path:
        trim_cmd = [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-i",
            str(encode_path),
            "-t",
            f"{post_trim_duration:.3f}",
            "-map",
            "0:v:0",
            "-map",
            "0:a:0",
            "-c",
            "copy",
            str(output_path),
        ]
        subprocess.run(trim_cmd, check=True, capture_output=True, text=True, timeout=120)
        try:
            encode_path.unlink()
        except OSError:
            pass
    return output_path


def concat_audio_for_segments(report, segments, output_path):
    audio_segments = []
    for segment in segments:
        if segment.audio_path:
            path = PROJECT_ROOT / segment.audio_path
            if path.exists():
                audio_segments.append(path)
    if not audio_segments:
        return None

    concat_path = Path(output_path).with_suffix(".audio.txt")
    concat_path.write_text(
        "\n".join(f"file {shlex.quote(str(path.resolve()))}" for path in audio_segments),
        encoding="utf-8",
    )
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_path),
            "-c:a",
            "aac",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=180,
    )
    return Path(output_path)


def render_report_video(report, ratio="16:9"):
    segments = list(report.segments)
    if not segments:
        raise ValueError("没有可渲染的口播段落，请先生成 AI 文案")

    output_root = task_dir(report.task_id) / "outputs" / report.report_code / ratio.replace(":", "x")
    frame_dir = output_root / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    output_root.mkdir(parents=True, exist_ok=True)

    frame_items = []
    previous_focus_node_id = None
    lead_in_duration = _source_card_duration_for(report=report, task=report.task, multi_report=False) if ratio == "16:9" else 0.0
    for index, segment in enumerate(segments, start=1):
        duration = segment.actual_duration_sec or segment.estimated_duration_sec or 30
        source_duration = lead_in_duration if ratio == "16:9" and index == 1 else 0.0
        if source_duration > 0:
            source_states = _source_card_visual_states(source_duration)
            for state_index, state in enumerate(source_states, start=1):
                source_frame_path = frame_dir / f"frame_{index:03d}_00_source_{state_index:02d}.jpg"
                render_landscape_source_card(report, segment, source_frame_path, reveal_progress=state.get("reveal_progress", 1.0))
                frame_items.append((source_frame_path, state.get("duration", source_duration)))
        remaining_duration = max(float(duration), 1.0)
        segment_focus_node_id = _segment_focus_node_id(segment)
        segment_context = SimpleNamespace(
            **getattr(segment, "__dict__", {}),
            previous_focus_node_id=previous_focus_node_id,
        )
        visual_states = _build_segment_visual_states(report, segment_context, remaining_duration)
        for state_index, state in enumerate(visual_states, start=1):
            frame_path = frame_dir / f"frame_{index:03d}_{state_index:02d}.jpg"
            frame_segment = SimpleNamespace(
                **getattr(segment, "__dict__", {}),
                visual_child_focus_id=state.get("child_focus_id"),
                transition_progress=state.get("transition_progress", 1.0),
                previous_focus_node_id=previous_focus_node_id,
            )
            if ratio == "9:16":
                render_portrait_frame(report, frame_segment, frame_path)
                width, height = 1080, 1920
            else:
                render_landscape_frame(report, frame_segment, frame_path)
                width, height = 1920, 1080
            frame_items.append((frame_path, state.get("duration", 1.0)))
        if segment_focus_node_id:
            previous_focus_node_id = segment_focus_node_id

    audio_path = concat_audio_for_segments(report, segments, output_root / "combined_audio.m4a")
    if audio_path and lead_in_duration > 0:
        audio_path = _concat_audio_with_silence_inserts(
            audio_path,
            [{"insert_at": 0.0, "duration": lead_in_duration}],
            output_root / "combined_audio_with_source.m4a",
        )
    output_name = safe_filename(f"{report.report_code}_{report.file_name}")[:120]
    output_path = output_root / f"{output_name}_{ratio.replace(':', 'x')}_{uuid.uuid4().hex[:8]}.mp4"
    make_concat_video(frame_items, output_path, width, height, audio_path=audio_path)
    duration = media_duration(output_path)
    return {
        "file_path": rel_path(output_path),
        "duration_sec": duration,
        "ratio": ratio,
        "video_type": "mindmap_focus",
    }


def _task_segments(task):
    segments = []
    for report in task.reports:
        segments.extend(list(report.segments))
    return sorted(segments, key=lambda item: (item.report.report_code if item.report else "", item.segment_index))


def _ratio_dimensions(ratio):
    return (1080, 1920) if ratio == "9:16" else (1920, 1080)


def _render_segment_preview_frame(report, segment, frame_path, ratio="16:9", previous_focus_node_id=None, duration=None):
    segment_duration = duration or segment.actual_duration_sec or segment.estimated_duration_sec or 12
    segment_context = SimpleNamespace(
        **getattr(segment, "__dict__", {}),
        previous_focus_node_id=previous_focus_node_id,
    )
    visual_states = _build_segment_visual_states(report, segment_context, segment_duration)
    state = visual_states[min(1, len(visual_states) - 1)] if visual_states else {"child_focus_id": None, "transition_progress": 1.0}
    frame_segment = SimpleNamespace(
        **getattr(segment, "__dict__", {}),
        visual_child_focus_id=state.get("child_focus_id"),
        transition_progress=state.get("transition_progress", 1.0),
        previous_focus_node_id=previous_focus_node_id,
    )
    if ratio == "9:16":
        render_portrait_frame(report, frame_segment, frame_path)
    else:
        render_landscape_frame(report, frame_segment, frame_path)
    return state


def _preview_segments_from_timeline(task, segments, limit=8):
    segment_map = {segment.id: segment for segment in segments}
    timeline_spans = sorted((task.result or {}).get("timeline_spans") or [], key=lambda item: item.get("start_sec") or 0)
    selected = []
    seen = set()
    for span in timeline_spans:
        if span.get("type") != "segment":
            continue
        segment = segment_map.get(span.get("segment_id"))
        if not segment or segment.id in seen:
            continue
        selected.append(segment)
        seen.add(segment.id)
        if len(selected) >= limit:
            break
    if selected:
        return selected
    return segments[:limit]


def _selected_preview_milestones(selected_milestones):
    items = []
    for item in selected_milestones or []:
        if not isinstance(item, dict):
            continue
        milestone_type = str(item.get("type") or "").strip()
        segment_id = item.get("segment_id")
        items.append({
            "type": milestone_type,
            "segment_id": str(segment_id) if segment_id else None,
            "label": str(item.get("label") or "").strip(),
        })
    return items


def _previous_focus_node_id_for_segment(segments, target_segment):
    previous_focus_node_id = None
    for segment in segments:
        if segment.id == target_segment.id:
            return previous_focus_node_id
        current_focus_node_id = _segment_focus_node_id(segment)
        if current_focus_node_id:
            previous_focus_node_id = current_focus_node_id
    return previous_focus_node_id


def _timeline_span_for_segment(task, segment):
    for span in (task.result or {}).get("timeline_spans") or []:
        if span.get("type") == "segment" and span.get("segment_id") == segment.id:
            return span
    return None


def _append_clipped_frame_item(frame_items, frame_path, duration, remaining):
    clipped = min(max(float(duration or 0.0), 0.05), max(float(remaining or 0.0), 0.0))
    if clipped <= 0:
        return 0.0
    frame_items.append((frame_path, clipped))
    return clipped


def _trim_audio_clip(audio_path, output_path, duration_sec=10.0, start_sec=0.0):
    if not audio_path:
        return None
    audio_path = Path(audio_path)
    if not audio_path.exists():
        return None
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-v",
            "error",
            "-ss",
            f"{float(start_sec or 0):.3f}",
            "-t",
            f"{float(duration_sec or 10):.3f}",
            "-i",
            str(audio_path),
            "-c:a",
            "aac",
            str(output_path),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return output_path if output_path.exists() else None


def render_task_keyframes(task, ratio="16:9", limit=8):
    segments = _task_segments(task)
    if not segments:
        raise ValueError("没有可渲染的口播段落，请先生成 AI 文案")

    output_root = task_dir(task.id) / "previews" / "keyframes" / ratio.replace(":", "x") / uuid.uuid4().hex[:8]
    frame_dir = output_root / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    selected_segments = _preview_segments_from_timeline(task, segments, limit=max(1, int(limit or 8)))
    file_paths = []
    segment_ids = []
    milestones = []

    if ratio == "16:9" and _is_theme_task(task):
        opening_path = frame_dir / "keyframe_000_theme_opening.jpg"
        render_landscape_theme_opening(task, opening_path, stage="network", progress=1.0)
        opening_rel = rel_path(opening_path)
        file_paths.append(opening_rel)
        milestones.append({
            "type": "theme_opening",
            "label": "主题开场",
            "file_path": opening_rel,
            "segment_id": None,
        })

    previous_focus_node_id = None
    for index, segment in enumerate(selected_segments, start=1):
        report = segment.report
        if not report:
            continue
        frame_path = frame_dir / f"keyframe_{index:03d}_{safe_filename(segment.segment_id or segment.id)}.jpg"
        _render_segment_preview_frame(report, segment, frame_path, ratio=ratio, previous_focus_node_id=previous_focus_node_id)
        frame_rel = rel_path(frame_path)
        segment_label = _segment_display_title(segment, analysis=report.analysis, max_chars=28)
        file_paths.append(frame_rel)
        segment_ids.append(segment.id)
        milestones.append({
            "type": "segment",
            "label": segment_label or segment.segment_id or f"段落 {index}",
            "file_path": frame_rel,
            "segment_id": segment.id,
            "segment_index": segment.segment_index,
            "report_code": report.report_code,
        })
        current_focus_node_id = _segment_focus_node_id(segment)
        if current_focus_node_id:
            previous_focus_node_id = current_focus_node_id

    if not file_paths:
        raise ValueError("没有生成有效关键帧")
    return {
        "preview_type": "keyframes",
        "ratio": ratio,
        "file_paths": file_paths,
        "file_path": file_paths[0],
        "segment_ids": segment_ids,
        "duration_sec": None,
        "renderer_version": REPORT_VIDEO_RENDER_PREVIEW_VERSION,
        "meta": {
            "frame_count": len(file_paths),
            "milestone_count": len(milestones),
            "milestones": milestones,
            "source": "timeline" if (task.result or {}).get("timeline_spans") else "segments",
        },
    }


def _preview_audio_for_milestone(task, segment, output_path, duration_sec):
    full_audio = (task.result or {}).get("full_audio") or {}
    if full_audio.get("file_path"):
        span = _timeline_span_for_segment(task, segment) if segment is not None else None
        start_sec = float((span or {}).get("start_sec") or 0.0)
        return _trim_audio_clip(
            PROJECT_ROOT / full_audio["file_path"],
            output_path,
            duration_sec=duration_sec,
            start_sec=start_sec,
        )
    if segment is not None and segment.audio_path:
        return _trim_audio_clip(PROJECT_ROOT / segment.audio_path, output_path, duration_sec=duration_sec)
    return None


def _render_task_opening_preview_clip(task, ratio, output_root, duration_sec):
    width, height = _ratio_dimensions(ratio)
    clip_dir = output_root / "opening"
    frame_dir = clip_dir / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    frame_items = []
    remaining = duration_sec
    opening_states = _theme_opening_visual_states(task, duration_sec)
    for state_index, state in enumerate(opening_states, start=1):
        if remaining <= 0:
            break
        frame_path = frame_dir / f"opening_{state_index:02d}.jpg"
        render_landscape_theme_opening(
            task,
            frame_path,
            stage=state.get("stage") or "covers",
            progress=state.get("progress", 1.0),
        )
        consumed = _append_clipped_frame_item(frame_items, frame_path, state.get("duration", 1.0), remaining)
        remaining -= consumed
    if remaining > 0 and frame_items:
        last_frame, last_duration = frame_items[-1]
        frame_items[-1] = (last_frame, float(last_duration or 0.0) + remaining)
    audio_path = None
    full_audio = (task.result or {}).get("full_audio") or {}
    if full_audio.get("file_path"):
        audio_path = _trim_audio_clip(
            PROJECT_ROOT / full_audio["file_path"],
            clip_dir / "opening_audio.m4a",
            duration_sec=duration_sec,
        )
    output_path = clip_dir / f"milestone_000_opening_{ratio.replace(':', 'x')}_{uuid.uuid4().hex[:8]}.mp4"
    make_concat_video(frame_items, output_path, width, height, audio_path=audio_path, transition_duration=0.0)
    return {
        "type": "theme_opening",
        "label": "主题开场",
        "file_path": rel_path(output_path),
        "segment_id": None,
        "duration_sec": media_duration(output_path),
    }


def _render_segment_preview_clip(task, segment, ratio, output_root, index, duration_sec, previous_focus_node_id=None):
    report = segment.report
    if not report:
        return None
    width, height = _ratio_dimensions(ratio)
    clip_dir = output_root / f"segment_{index:03d}_{safe_filename(segment.segment_id or segment.id)}"
    frame_dir = clip_dir / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    frame_items = []
    remaining = duration_sec
    segment_duration = min(float(segment.actual_duration_sec or segment.estimated_duration_sec or duration_sec), duration_sec)
    segment_context = SimpleNamespace(
        **getattr(segment, "__dict__", {}),
        previous_focus_node_id=previous_focus_node_id,
    )
    visual_states = _build_segment_visual_states(report, segment_context, segment_duration)
    if not visual_states:
        visual_states = [{"child_focus_id": None, "duration": segment_duration, "transition_progress": 1.0}]
    for state_index, state in enumerate(visual_states, start=1):
        if remaining <= 0:
            break
        frame_path = frame_dir / f"clip_{index:03d}_{state_index:02d}.jpg"
        state_segment = SimpleNamespace(
            **getattr(segment, "__dict__", {}),
            visual_child_focus_id=state.get("child_focus_id"),
            transition_progress=state.get("transition_progress", 1.0),
            previous_focus_node_id=previous_focus_node_id,
        )
        if ratio == "9:16":
            render_portrait_frame(report, state_segment, frame_path)
        else:
            render_landscape_frame(report, state_segment, frame_path)
        consumed = _append_clipped_frame_item(frame_items, frame_path, state.get("duration", segment_duration), remaining)
        remaining -= consumed
    if not frame_items:
        return None
    if remaining > 0:
        last_frame, last_duration = frame_items[-1]
        frame_items[-1] = (last_frame, float(last_duration or 0.0) + remaining)
    audio_path = _preview_audio_for_milestone(task, segment, clip_dir / "clip_audio.m4a", duration_sec)
    output_path = clip_dir / f"milestone_{index:03d}_{safe_filename(segment.segment_id or segment.id)}_{ratio.replace(':', 'x')}_{uuid.uuid4().hex[:8]}.mp4"
    make_concat_video(
        frame_items,
        output_path,
        width,
        height,
        audio_path=audio_path,
        transition_duration=0.0,
    )
    segment_label = _segment_display_title(segment, analysis=report.analysis, max_chars=28)
    return {
        "type": "segment",
        "label": segment_label or segment.segment_id or f"段落 {index}",
        "file_path": rel_path(output_path),
        "segment_id": segment.id,
        "segment_index": segment.segment_index,
        "report_code": report.report_code,
        "duration_sec": media_duration(output_path),
    }


def render_task_preview_clip(task, ratio="16:9", duration_sec=10.0, limit=6, selected_milestones=None):
    segments = _task_segments(task)
    if not segments:
        raise ValueError("没有可渲染的口播段落，请先生成 AI 文案")

    preview_duration = max(3.0, min(float(duration_sec or 10.0), 30.0))
    output_root = task_dir(task.id) / "previews" / "clips" / ratio.replace(":", "x") / uuid.uuid4().hex[:8]
    output_root.mkdir(parents=True, exist_ok=True)

    file_paths = []
    segment_ids = []
    milestones = []
    previous_focus_node_id = None
    selected_items = _selected_preview_milestones(selected_milestones)
    selected_segment_ids = [item["segment_id"] for item in selected_items if item.get("segment_id")]
    selected_mode = bool(selected_items)
    include_opening = any(item.get("type") == "theme_opening" for item in selected_items)

    if ratio == "16:9" and _is_theme_task(task) and (include_opening or not selected_mode):
        opening = _render_task_opening_preview_clip(task, ratio, output_root, min(preview_duration, 10.0))
        file_paths.append(opening["file_path"])
        milestones.append(opening)

    if selected_mode:
        segment_map = {segment.id: segment for segment in segments}
        selected_segments = []
        seen = set()
        for segment_id in selected_segment_ids:
            segment = segment_map.get(segment_id)
            if segment and segment.id not in seen:
                selected_segments.append(segment)
                seen.add(segment.id)
    else:
        selected_segments = _preview_segments_from_timeline(task, segments, limit=max(1, int(limit or 6)))
    for index, segment in enumerate(selected_segments, start=1):
        if selected_mode:
            previous_focus_node_id = _previous_focus_node_id_for_segment(segments, segment)
        milestone = _render_segment_preview_clip(
            task,
            segment,
            ratio,
            output_root,
            index,
            preview_duration,
            previous_focus_node_id=previous_focus_node_id,
        )
        if milestone:
            file_paths.append(milestone["file_path"])
            milestones.append(milestone)
            segment_ids.append(segment.id)
        current_focus_node_id = _segment_focus_node_id(segment)
        if current_focus_node_id:
            previous_focus_node_id = current_focus_node_id

    if not file_paths:
        raise ValueError("没有生成有效预览片段画面")
    return {
        "preview_type": "preview_clip",
        "ratio": ratio,
        "file_path": file_paths[0],
        "file_paths": file_paths,
        "segment_ids": segment_ids,
        "duration_sec": sum(float(item.get("duration_sec") or 0.0) for item in milestones),
        "renderer_version": REPORT_VIDEO_RENDER_PREVIEW_VERSION,
        "meta": {
            "clip_count": len(file_paths),
            "milestone_count": len(milestones),
            "milestones": milestones,
            "mode": "selected" if selected_mode else "group",
            "requested_milestones": selected_items,
            "per_clip_duration_sec": preview_duration,
            "source": "timeline" if (task.result or {}).get("timeline_spans") else "segments",
        },
    }


def _report_for_context_span(task, span, segments):
    for segment in segments:
        timeline = (segment.raw_payload or {}).get("timeline") or {}
        if timeline.get("start_sec") is not None and float(timeline["start_sec"]) >= float(span.get("start_sec") or 0):
            return segment.report
    return task.reports[0] if task.reports else None


def _theme_opening_span_info(timeline_spans, segment_map):
    def _value(obj, key, default=None):
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    for span in timeline_spans or []:
        if _value(span, "type") != "segment":
            continue
        segment_id = _value(span, "segment_id")
        segment = segment_map.get(segment_id)
        segment_type = str(_value(segment, "segment_type", "") or "").lower()
        if isinstance(segment, dict):
            focus_id = str(segment.get("focus_node_id") or segment.get("mindmap_node_id") or "")
        else:
            focus_id = str(_segment_focus_node_id(segment) or "")
        if segment_type == "topic_opening" or focus_id == "node_overview":
            start_sec = float(_value(span, "start_sec", _value(span, "start", 0.0)) or 0.0)
            end_sec = float(_value(span, "end_sec", _value(span, "end", start_sec)) or start_sec)
            span_duration = max(end_sec - start_sec, 0.0)
            if span_duration <= 0:
                continue
            opening_duration = min(span_duration, 12.0)
            if span_duration >= 5.0:
                opening_duration = max(opening_duration, 5.0)
            return {
                "segment_id": segment_id,
                "duration": round(opening_duration, 3),
                "span_duration": round(span_duration, 3),
            }
    return {"segment_id": None, "duration": 0.0, "span_duration": 0.0}


def render_task_video(task, ratio="16:9"):
    segments = _task_segments(task)
    segment_map = {segment.id: segment for segment in segments}
    original_timeline_spans = sorted((task.result or {}).get("timeline_spans") or [], key=lambda item: item.get("start_sec") or 0)
    full_audio = (task.result or {}).get("full_audio") or {}
    audio_path = PROJECT_ROOT / full_audio.get("file_path", "") if full_audio.get("file_path") else None
    theme_mode = ratio == "16:9" and _is_theme_task(task)
    if not original_timeline_spans:
        original_timeline_spans = [
            {
                "type": "segment",
                "segment_id": segment.id,
                "start_sec": 0,
                "end_sec": segment.actual_duration_sec or segment.estimated_duration_sec or 30,
                "text": segment.voice_text or "",
            }
            for segment in segments
        ]
    if not original_timeline_spans:
        raise ValueError("没有可渲染的时间轴，请先生成 AI 文案并上传音频时间轴")

    timeline_spans = original_timeline_spans
    source_card_inserts = []
    total_source_card_duration = 0.0
    if ratio == "16:9" and not theme_mode:
        timeline_spans, source_card_inserts, total_source_card_duration = _expand_timeline_spans_with_source_cards(
            task,
            original_timeline_spans,
            segment_map,
            segments,
        )

    output_root = task_dir(task.id) / "outputs" / "task" / ratio.replace(":", "x")
    frame_dir = output_root / "frames"
    frame_dir.mkdir(parents=True, exist_ok=True)
    output_root.mkdir(parents=True, exist_ok=True)

    frame_items = []
    theme_opening = _theme_opening_span_info(timeline_spans, segment_map) if theme_mode else {"segment_id": None, "duration": 0.0}
    if theme_mode:
        opening_states = _theme_opening_visual_states(task, theme_opening.get("duration"))
        for state_index, state in enumerate(opening_states, start=1):
            frame_path = frame_dir / f"frame_000_{state_index:02d}_theme_opening.jpg"
            render_landscape_theme_opening(
                task,
                frame_path,
                stage=state.get("stage") or "covers",
                progress=state.get("progress", 1.0),
            )
            frame_items.append((frame_path, state.get("duration", 1.0)))

    previous_focus_node_id = None
    for index, span in enumerate(timeline_spans, start=1):
        if span.get("type") == "source_card":
            report = next((segment.report for segment in segments if segment.report_id == span.get("report_id")), None)
            if report:
                reference_segment = next((segment for segment in segments if segment.report_id == span.get("report_id")), None)
                source_states = _source_card_visual_states(max(float(span.get("end_sec") or 0) - float(span.get("start_sec") or 0), 0.5))
                for state_index, state in enumerate(source_states, start=1):
                    frame_path = frame_dir / f"frame_{index:03d}_{state_index:02d}_source.jpg"
                    render_landscape_source_card(report, reference_segment, frame_path, reveal_progress=state.get("reveal_progress", 1.0))
                    frame_items.append((frame_path, state.get("duration", 1.0)))
            continue

        start_sec = float(span.get("start_sec") or 0)
        end_sec = float(span.get("end_sec") or start_sec + 1)
        duration = max(end_sec - start_sec, 0.05)
        segment = segment_map.get(span.get("segment_id"))
        if theme_mode and segment and span.get("segment_id") == theme_opening.get("segment_id"):
            opening_consumed = min(float(theme_opening.get("duration") or 0.0), max(end_sec - start_sec, 0.0))
            start_sec += opening_consumed
            duration = max(end_sec - start_sec, 0.0)
            if duration < 0.6:
                current_focus_node_id = _segment_focus_node_id(segment)
                if current_focus_node_id:
                    previous_focus_node_id = current_focus_node_id
                continue
        report = segment.report if segment else _report_for_context_span(task, span, segments)
        if not report:
            continue
        frame_segment = segment or SimpleNamespace(
            segment_title="开场 / 过渡",
            voice_text=span.get("text") or "",
            focus_node_id=None,
            actual_duration_sec=duration,
            estimated_duration_sec=duration,
        )
        frame_segment_context = SimpleNamespace(
            **getattr(frame_segment, "__dict__", {}),
            previous_focus_node_id=previous_focus_node_id,
        )
        visual_states = _build_segment_visual_states(report, frame_segment_context, duration) if segment else [{"child_focus_id": None, "duration": duration, "transition_progress": 1.0}]
        for state_index, state in enumerate(visual_states, start=1):
            state_segment = SimpleNamespace(
                **getattr(frame_segment, "__dict__", {}),
                visual_child_focus_id=state.get("child_focus_id"),
                transition_progress=state.get("transition_progress", 1.0),
                previous_focus_node_id=previous_focus_node_id,
            )
            frame_path = frame_dir / f"frame_{index:03d}_{state_index:02d}.jpg"
            if ratio == "9:16":
                render_portrait_frame(report, state_segment, frame_path)
                width, height = 1080, 1920
            else:
                render_landscape_frame(report, state_segment, frame_path)
                width, height = 1920, 1080
            frame_items.append((frame_path, state.get("duration", 1.0)))
        current_focus_node_id = _segment_focus_node_id(frame_segment)
        if current_focus_node_id:
            previous_focus_node_id = current_focus_node_id

    if not frame_items:
        raise ValueError("时间轴没有生成有效画面")
    if audio_path and not audio_path.exists():
        audio_path = None

    output_name = safe_filename(task.task_name)[:120]
    output_path = output_root / f"{output_name}_{ratio.replace(':', 'x')}_{uuid.uuid4().hex[:8]}.mp4"
    subtitle_path = PROJECT_ROOT / full_audio.get("subtitle_path", "") if full_audio.get("subtitle_path") else None
    if subtitle_path and not subtitle_path.exists():
        subtitle_path = None
    if audio_path and source_card_inserts:
        audio_path = _concat_audio_with_silence_inserts(
            audio_path,
            source_card_inserts,
            output_root / "audio_with_source_cards.m4a",
        )
    if subtitle_path and source_card_inserts:
        subtitle_path = _shift_srt_by_inserts(
            subtitle_path,
            source_card_inserts,
            output_root / f"{subtitle_path.stem}_with_source_offset.srt",
        )
    if subtitle_path and theme_mode:
        frame_items = _burn_subtitles_into_frame_items(
            frame_items,
            subtitle_path,
            output_root / "subtitle_frames",
            width,
            height,
        )
        subtitle_path = None
    if audio_path and frame_items:
        audio_duration = media_duration(audio_path)
        frame_duration = sum(max(float(duration or 0.0), 0.0) for _, duration in frame_items)
        if audio_duration > frame_duration:
            last_frame, last_duration = frame_items[-1]
            frame_items[-1] = (last_frame, float(last_duration or 0.0) + (audio_duration - frame_duration) + 0.05)
    transition_duration = 0.0 if theme_mode else 0.6
    make_concat_video(
        frame_items,
        output_path,
        width,
        height,
        audio_path=audio_path,
        subtitle_path=subtitle_path,
        transition_duration=transition_duration,
    )
    duration = media_duration(output_path)
    return {
        "file_path": rel_path(output_path),
        "duration_sec": duration,
        "ratio": ratio,
        "video_type": "task_theme_storyline" if theme_mode else "task_mindmap_focus",
        "subtitle_calibration": full_audio.get("subtitle_calibration") or (task.result or {}).get("subtitle_calibration"),
    }
