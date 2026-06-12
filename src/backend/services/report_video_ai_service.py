import json
import logging
import os
import re
from datetime import datetime

import requests

from models import db
from models.report_video import (
    ReportVideoAnalysis,
    ReportVideoMindmap,
    ReportVideoReport,
    ReportVideoSegment,
    ReportVideoTask,
)
from services.report_video_service import read_task_text_file
from services.report_video_prompt_service import render_prompt


logger = logging.getLogger(__name__)


def _compact_text(text, limit=12000):
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:limit]


def _trim(value, limit, default=""):
    text = str(value if value is not None else default).strip()
    return text[:limit]


def _safe_int(value, default=0, minimum=None, maximum=None):
    try:
        if isinstance(value, str):
            match = re.search(r"-?\d+(?:\.\d+)?", value)
            if not match:
                raise ValueError("no numeric value")
            number = float(match.group(0))
        else:
            number = float(value)
        result = int(round(number))
    except (TypeError, ValueError):
        result = int(default)
    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def _safe_list(value, limit=8):
    if isinstance(value, list):
        items = value
    elif value:
        items = [value]
    else:
        items = []
    result = []
    for item in items[:limit]:
        if isinstance(item, dict):
            text = item.get("text") or item.get("label") or item.get("title") or json.dumps(item, ensure_ascii=False)
        else:
            text = item
        text = str(text).strip()
        if text:
            result.append(text[:500])
    return result


def _safe_float(value, default=0.0, minimum=None, maximum=None):
    try:
        if isinstance(value, str):
            match = re.search(r"-?\d+(?:\.\d+)?", value)
            if not match:
                raise ValueError("no numeric value")
            number = float(match.group(0))
        else:
            number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if minimum is not None:
        number = max(float(minimum), number)
    if maximum is not None:
        number = min(float(maximum), number)
    return number


def _report_card_id(report):
    code = str(report.report_code or "").strip()
    if code.isdigit():
        return f"R{int(code):03d}"
    return f"R{code or str(report.id)[:6]}"


def _safe_node_id(value, fallback):
    text = str(value or fallback).strip()
    text = re.sub(r"[^\w:-]+", "_", text)
    text = text.strip("_") or fallback
    return text[:64]


def _sentences(text):
    parts = re.split(r"(?<=[。！？!?；;])\s*|\n+", text or "")
    cleaned = [part.strip() for part in parts if len(part.strip()) >= 18]
    return cleaned


def _extract_title(report, text):
    for line in (text or "").splitlines():
        line = line.strip()
        if 8 <= len(line) <= 80:
            return line
    return report.file_name.rsplit(".", 1)[0]


def _pick_keywords(text, limit=6):
    patterns = [
        r"AI|GPU|CPU|CPO|ASIC|ESS|WSTS|光模块|光通信|半导体|量子计算|机器人|新能源|储能|电池|电气设备|公用事业|汽车|医药|银行|地产|消费|算力|数据中心|能源|软件|硬件|互联网",
        r"增长|景气|订单|利润|估值|拐点|需求|供给|国产替代|出海|催化|风险|资本支出|科技主权|月度销售|并购|监管",
    ]
    keywords = []
    for pattern in patterns:
        for match in re.findall(pattern, text or "", flags=re.IGNORECASE):
            value = str(match).upper() if len(str(match)) <= 4 else str(match)
            if value not in keywords:
                keywords.append(value)
    return keywords[:limit]


def _read_report_text_safely(report, limit=12000):
    try:
        if getattr(report, "cleaned_text_path", None):
            return _compact_text(read_task_text_file(report.cleaned_text_path), limit)
    except Exception:
        return ""
    return ""


def _filename_theme_terms(file_name, limit=8):
    stem = str(file_name or "").rsplit(".", 1)[0]
    stem = re.sub(r"^\s*伯恩斯坦[-_ ]*", "", stem)
    stem = re.sub(r"[-_ ]?20\d{6}$", "", stem)
    stem = re.sub(r"（[^）]{1,24}）|\([^)]{1,24}\)", "", stem)
    parts = re.split(r"[-：:；;，,、~｜|&]+", stem)
    terms = []
    for part in parts:
        text = re.sub(r"\s+", "", part).strip()
        if not text or text in {"伯恩斯坦"}:
            continue
        if len(text) > 18:
            text = text[:18]
        if text not in terms:
            terms.append(text)
        if len(terms) >= limit:
            break
    return terms


def _split_evenly(items, count):
    if count <= 0:
        return []
    if not items:
        return [[] for _ in range(count)]
    buckets = []
    step = max(len(items) / count, 1)
    for index in range(count):
        start = round(index * step)
        end = round((index + 1) * step)
        buckets.append(items[start:end] or items[max(0, min(len(items) - 1, start)):start + 1])
    return buckets


def _fallback_payload(report, segment_count):
    text = read_task_text_file(report.cleaned_text_path)
    compact = _compact_text(text, 12000)
    sentences = _sentences(compact)
    title = _extract_title(report, text)
    keywords = _pick_keywords(compact)
    core_view = sentences[0] if sentences else f"{title} 的核心信息需要结合正文进一步提炼。"
    key_points = sentences[1:6] if len(sentences) > 1 else [core_view]
    buckets = _split_evenly(sentences[: max(segment_count * 3, segment_count)], segment_count)

    nodes = [{"id": "root", "label": title[:40], "type": "center", "children": []}]
    segments = []
    for index, bucket in enumerate(buckets, start=1):
        node_id = f"node_{index:03d}"
        heading = keywords[index - 1] if index - 1 < len(keywords) else f"要点 {index}"
        node_title = f"{heading}：{(bucket[0] if bucket else title)[:28]}"
        voice_text = "。".join(sentence.rstrip("。") for sentence in bucket[:2]).strip()
        if not voice_text:
            voice_text = core_view
        if not voice_text.endswith(("。", "！", "？")):
            voice_text += "。"
        nodes[0]["children"].append(node_id)
        nodes.append({
            "id": node_id,
            "label": node_title[:42],
            "type": "point",
            "children": [],
        })
        segments.append({
            "segment_id": f"seg_{index:03d}",
            "segment_index": index,
            "segment_type": "key_point",
            "segment_title": node_title[:80],
            "voice_text": voice_text[:420],
            "estimated_duration_sec": max(20, min(60, round(len(voice_text) / 5))),
            "focus_node_id": node_id,
        })

    return {
        "report_title": title,
        "institution": "",
        "theme": keywords[0] if keywords else "",
        "opening_text": f"大家好，今天快速看一份研报：{title}。",
        "core_view": core_view[:600],
        "key_points": key_points[:6],
        "video_value_score": 60 if report.cleaned_text_chars > 1000 else 35,
        "recommended_video_type": "daily_digest",
        "recommended_duration_sec": sum(item["estimated_duration_sec"] for item in segments),
        "reason": "本地抽取式生成：用于无 DeepSeek API key 时保证流程可用。",
        "closing_text": "以上就是这份研报的核心观点。",
        "mindmap": {
            "center": title[:80],
            "nodes": nodes,
        },
        "segments": segments,
        "provider": "local_fallback",
    }


def _json_from_text(value):
    text = (value or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match:
        text = match.group(0)
    return json.loads(text)


def _deepseek_json(system_prompt, user_prompt, *, temperature=0.2, timeout=90):
    api_key = os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("REPORT_VIDEO_LLM_API_KEY")
    if not api_key:
        return None

    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    model = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
    response = requests.post(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
        },
        timeout=timeout,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        body = response.text[:800] if response is not None else ""
        raise RuntimeError(f"DeepSeek API 请求失败: HTTP {response.status_code} {body}") from exc
    content = response.json()["choices"][0]["message"]["content"]
    payload = _json_from_text(content)
    if isinstance(payload, dict):
        payload["provider"] = "deepseek"
    return payload


def _deepseek_payload(report, segment_count):
    text = _compact_text(read_task_text_file(report.cleaned_text_path), 12000)
    system_prompt, user_prompt = render_prompt(
        "report_ai_content",
        {
            "segment_count": segment_count,
            "file_name": report.file_name,
            "text": text,
        },
    )
    return _deepseek_json(system_prompt, user_prompt, temperature=0.2, timeout=90)


def _normalize_payload(report, payload, segment_count):
    fallback = _fallback_payload(report, segment_count)
    payload = payload or fallback
    payload["report_title"] = _trim(payload.get("report_title") or fallback["report_title"], 500)
    payload["institution"] = _trim(payload.get("institution") or "", 255)
    payload["theme"] = _trim(payload.get("theme") or fallback["theme"], 80)
    payload["opening_text"] = _trim(payload.get("opening_text") or fallback.get("opening_text") or "", 500)
    payload["core_view"] = _trim(payload.get("core_view") or fallback["core_view"], 4000)
    payload["key_points"] = _safe_list(payload.get("key_points") or fallback["key_points"], 8)
    payload["video_value_score"] = _safe_int(payload.get("video_value_score"), fallback["video_value_score"], 0, 100)
    payload["recommended_video_type"] = _trim(payload.get("recommended_video_type") or "daily_digest", 60)
    payload["reason"] = _trim(payload.get("reason") or "", 4000)
    payload["closing_text"] = _trim(payload.get("closing_text") or fallback.get("closing_text") or "", 500)

    mindmap = payload.get("mindmap") if isinstance(payload.get("mindmap"), dict) else {}
    raw_nodes = mindmap.get("nodes") if isinstance(mindmap.get("nodes"), list) else []
    nodes = []
    node_id_map = {}
    for index, node in enumerate(raw_nodes):
        if not isinstance(node, dict):
            continue
        raw_id = str(node.get("id") or f"node_{index:03d}")
        safe_id = _safe_node_id(raw_id, "root" if index == 0 else f"node_{index:03d}")
        node_id_map[raw_id] = safe_id
        nodes.append({
            "id": safe_id,
            "label": _trim(node.get("label") or node.get("title") or safe_id, 80),
            "type": _trim(node.get("type") or ("center" if safe_id == "root" else "point"), 40),
            "children": [
                _safe_node_id(child, f"node_{child_index + 1:03d}")
                for child_index, child in enumerate(node.get("children") or [])
            ][:12],
        })
    node_ids = {node["id"] for node in nodes}
    if "root" not in node_ids:
        nodes.insert(0, {"id": "root", "label": payload["report_title"][:40], "type": "center", "children": []})
        node_ids.add("root")

    normalized_segments = []
    for index, item in enumerate((payload.get("segments") or [])[:segment_count], start=1):
        if not isinstance(item, dict):
            continue
        raw_focus_node_id = str(item.get("focus_node_id") or f"node_{index:03d}")
        focus_node_id = node_id_map.get(raw_focus_node_id) or _safe_node_id(raw_focus_node_id, f"node_{index:03d}")
        if focus_node_id not in node_ids:
            nodes.append({
                "id": focus_node_id,
                "label": _trim(item.get("segment_title") or f"要点 {index}", 42),
                "type": "point",
                "children": [],
            })
            node_ids.add(focus_node_id)
        voice_text = item.get("voice_text") or fallback["segments"][min(index - 1, len(fallback["segments"]) - 1)]["voice_text"]
        normalized_segments.append({
            "segment_id": f"seg_{index:03d}",
            "segment_index": index,
            "segment_type": _trim(item.get("segment_type") or "key_point", 60),
            "segment_title": _trim(item.get("segment_title") or f"要点 {index}", 255),
            "voice_text": _trim(voice_text, 2000),
            "estimated_duration_sec": _safe_int(item.get("estimated_duration_sec"), 30, 5, 600),
            "focus_node_id": focus_node_id,
        })

    if not normalized_segments:
        normalized_segments = fallback["segments"]
        for item in normalized_segments:
            if item["focus_node_id"] not in node_ids:
                nodes.append({"id": item["focus_node_id"], "label": item["segment_title"][:42], "type": "point", "children": []})

    payload["recommended_duration_sec"] = _safe_int(
        payload.get("recommended_duration_sec"),
        sum(item.get("estimated_duration_sec") or 0 for item in normalized_segments) or fallback["recommended_duration_sec"],
        5,
        3600,
    )
    payload["mindmap"] = {
        "center": _trim(mindmap.get("center") or payload["report_title"], 255),
        "nodes": nodes,
    }
    payload["segments"] = normalized_segments
    return payload


def generate_report_ai_content(report: ReportVideoReport, segment_count=4):
    if report.parse_status != "parsed":
        raise ValueError("只有解析完成的文本型 PDF 可以生成 AI 文案")

    provider_error = None
    try:
        payload = _deepseek_payload(report, segment_count)
    except Exception as exc:
        provider_error = str(exc)
        logger.warning("Report video DeepSeek generation failed for report %s: %s", report.id, provider_error)
        payload = None

    payload = _normalize_payload(report, payload, segment_count)
    if provider_error and payload.get("provider") == "local_fallback":
        payload["provider_error"] = provider_error

    ReportVideoAnalysis.query.filter_by(report_id=report.id).delete()
    ReportVideoMindmap.query.filter_by(report_id=report.id).delete()
    ReportVideoSegment.query.filter_by(report_id=report.id).delete()
    db.session.flush()

    analysis = ReportVideoAnalysis(
        report_id=report.id,
        report_title=payload.get("report_title"),
        institution=payload.get("institution"),
        theme=payload.get("theme"),
        core_view=payload.get("core_view"),
        key_points=payload.get("key_points") or [],
        video_value_score=payload.get("video_value_score"),
        recommended_video_type=payload.get("recommended_video_type"),
        recommended_duration_sec=payload.get("recommended_duration_sec"),
        reason=payload.get("reason"),
        raw_payload=payload,
    )
    db.session.add(analysis)

    mindmap = ReportVideoMindmap(
        report_id=report.id,
        center=(payload.get("mindmap") or {}).get("center") or payload.get("report_title"),
        nodes=(payload.get("mindmap") or {}).get("nodes") or [],
        raw_payload=payload.get("mindmap") or {},
    )
    db.session.add(mindmap)

    for index, item in enumerate(payload.get("segments") or [], start=1):
        segment = ReportVideoSegment(
            task_id=report.task_id,
            report_id=report.id,
            segment_id=f"{report.report_code}_{item.get('segment_id') or f'seg_{index:03d}'}",
            segment_index=index,
            segment_type=item.get("segment_type"),
            segment_title=item.get("segment_title"),
            voice_text=item.get("voice_text"),
            estimated_duration_sec=item.get("estimated_duration_sec"),
            focus_node_id=item.get("focus_node_id"),
            status="script_ready",
            raw_payload=item,
        )
        db.session.add(segment)

    report.ai_status = "script_ready"
    report.error_message = None
    report.updated_at = datetime.utcnow()
    return payload


def _fallback_quick_card(report: ReportVideoReport):
    text = _read_report_text_safely(report, 8000)
    title = report.analysis.report_title if report.analysis else _extract_title(report, text)
    institution = report.analysis.institution if report.analysis else ""
    theme = report.analysis.theme if report.analysis else ""
    key_points = report.analysis.key_points if report.analysis else _sentences(text)[:5]
    filename_terms = _filename_theme_terms(report.file_name, 8)
    keywords = _pick_keywords(" ".join([text, report.file_name or ""]), 8)
    merged_terms = []
    for item in [*keywords, *filename_terms]:
        if item and item not in merged_terms:
            merged_terms.append(item)
    primary_theme = theme or (merged_terms[0] if merged_terms else "研报主题")
    score = report.analysis.video_value_score if report.analysis and report.analysis.video_value_score is not None else (
        8 if getattr(report, "cleaned_text_chars", 0) >= 3000 else 5
    )
    one_sentence = (report.analysis.core_view if report.analysis else (key_points[0] if key_points else title)) or title
    if not text:
        title = str(report.file_name or "").rsplit(".", 1)[0]
        institution = institution or ("伯恩斯坦" if "伯恩斯坦" in str(report.file_name or "") else "")
        one_sentence = f"该研报标题指向“{primary_theme}”方向，正文暂未完成 OCR，先用于批量主题策划占位。"
    return {
        "report_id": _report_card_id(report),
        "db_report_id": report.id,
        "report_code": report.report_code,
        "file_name": report.file_name,
        "report_title": _trim(title, 500),
        "institution": _trim(institution, 255) or None,
        "report_date": None,
        "primary_theme": _trim(primary_theme, 120),
        "secondary_themes": merged_terms[1:4],
        "theme_tags": merged_terms[:6] or [primary_theme[:20]],
        "companies": [],
        "industries": merged_terms[:4],
        "assets": merged_terms[:4],
        "one_sentence_summary": _trim(one_sentence, 500),
        "core_view": _trim(report.analysis.core_view if report.analysis else one_sentence, 1200),
        "key_points": _safe_list(key_points, 5),
        "why_it_matters": _trim(
            report.analysis.reason if report.analysis else (
                "PDF 正文暂不可用，基于文件名生成的主题策划占位卡。" if not text
                else "本地抽取式摘要，用于批量主题策划兜底。"
            ),
            1000,
        ),
        "suitable_for_video": bool(score and int(score) >= 5),
        "video_value_score": _safe_float(score, 5, 0, 10),
        "recommended_usage": "topic_collection",
        "provider": "local_fallback" if text else "metadata_fallback",
    }


def _normalize_quick_card(report: ReportVideoReport, payload):
    fallback = _fallback_quick_card(report)
    payload = payload if isinstance(payload, dict) else {}
    score = _safe_float(payload.get("video_value_score"), fallback["video_value_score"], 0, 10)
    return {
        "report_id": _trim(payload.get("report_id") or fallback["report_id"], 32),
        "db_report_id": report.id,
        "report_code": report.report_code,
        "file_name": report.file_name,
        "report_title": _trim(payload.get("report_title") or fallback["report_title"], 500),
        "institution": _trim(payload.get("institution") or fallback.get("institution") or "", 255) or None,
        "report_date": _trim(payload.get("report_date") or "", 32) or None,
        "primary_theme": _trim(payload.get("primary_theme") or fallback["primary_theme"], 120),
        "secondary_themes": _safe_list(payload.get("secondary_themes") or fallback["secondary_themes"], 6),
        "theme_tags": _safe_list(payload.get("theme_tags") or fallback["theme_tags"], 8),
        "companies": _safe_list(payload.get("companies"), 12),
        "industries": _safe_list(payload.get("industries") or fallback["industries"], 8),
        "assets": _safe_list(payload.get("assets") or fallback["assets"], 8),
        "one_sentence_summary": _trim(payload.get("one_sentence_summary") or fallback["one_sentence_summary"], 600),
        "core_view": _trim(payload.get("core_view") or fallback["core_view"], 1500),
        "key_points": _safe_list(payload.get("key_points") or fallback["key_points"], 5),
        "why_it_matters": _trim(payload.get("why_it_matters") or fallback["why_it_matters"], 1000),
        "suitable_for_video": bool(payload.get("suitable_for_video", fallback["suitable_for_video"])),
        "video_value_score": round(score, 2),
        "recommended_usage": _trim(payload.get("recommended_usage") or fallback["recommended_usage"], 40),
        "provider": payload.get("provider") or fallback["provider"],
    }


def generate_report_quick_card(report: ReportVideoReport):
    report_id = _report_card_id(report)
    text = _read_report_text_safely(report, 10000)
    if not text:
        return _normalize_quick_card(report, _fallback_quick_card(report))

    system_prompt, user_prompt = render_prompt(
        "report_quick_card",
        {
            "report_id": report_id,
            "file_name": report.file_name,
            "text": text,
        },
    )

    provider_error = None
    try:
        payload = _deepseek_json(system_prompt, user_prompt, temperature=0.15, timeout=90)
    except Exception as exc:
        provider_error = str(exc)
        logger.warning("Report quick card DeepSeek failed for report %s: %s", report.id, provider_error)
        payload = None

    card = _normalize_quick_card(report, payload)
    if provider_error and card.get("provider") == "local_fallback":
        card["provider_error"] = provider_error
    return card


def _fallback_topics(report_cards):
    buckets = {}
    for card in report_cards:
        tags = card.get("theme_tags") or []
        key = card.get("primary_theme") or (tags[0] if tags else "综合研报主线")
        key = str(key or "综合研报主线").strip()[:32]
        if not key:
            key = "综合研报主线"
        buckets.setdefault(key, []).append(card)

    topics = []
    sorted_buckets = sorted(
        buckets.items(),
        key=lambda item: (
            len(item[1]),
            sum(_safe_float(card.get("video_value_score"), 0) for card in item[1]),
        ),
        reverse=True,
    )
    for index, (theme, cards) in enumerate(sorted_buckets[:10], start=1):
        institutions = []
        reports = []
        key_points = []
        for card in cards:
            reports.append(card.get("report_id"))
            if card.get("institution") and card["institution"] not in institutions:
                institutions.append(card["institution"])
            for point in card.get("key_points") or []:
                if point not in key_points:
                    key_points.append(point)
        avg_score = sum(_safe_float(card.get("video_value_score"), 5) for card in cards) / max(len(cards), 1)
        internal_heat = min(10.0, round(avg_score + min(len(cards), 6) * 0.45, 2))
        topic_id = f"T{index:03d}"
        summary = cards[0].get("one_sentence_summary") or cards[0].get("core_view") or theme
        topics.append({
            "topic_id": topic_id,
            "topic_name": theme if len(theme) > 6 else f"{theme}相关机构研报主线",
            "topic_category": theme,
            "reports": [item for item in reports if item],
            "report_count": len(cards),
            "institutions": institutions[:8],
            "topic_summary": _trim(summary, 800),
            "common_views": _safe_list(key_points, 5),
            "conflicting_views": [],
            "representative_reports": [item for item in reports[:3] if item],
            "internal_heat_score": internal_heat,
            "video_priority_score": internal_heat,
            "recommended_video_angle": f"从 {len(cards)} 份研报里梳理“{theme}”主线的共识与分歧。",
            "recommended_title": f"{theme}：今天这批研报共同指向的主线",
            "why_it_is_worth_making": f"该主题在上传研报中出现 {len(cards)} 次，具备批量研报聚合解读价值。",
        })
    return {"topics": topics, "provider": "local_fallback"}


def _normalize_topic(topic, index, report_cards_by_id):
    topic_id = _trim(topic.get("topic_id") or f"T{index:03d}", 32)
    reports = [
        str(item).strip()
        for item in (topic.get("reports") or topic.get("report_ids") or [])
        if str(item).strip()
    ]
    reports = [report_id for report_id in reports if report_id in report_cards_by_id] or reports[:12]
    institutions = _safe_list(topic.get("institutions"), 12)
    if not institutions:
        seen = []
        for report_id in reports:
            institution = (report_cards_by_id.get(report_id) or {}).get("institution")
            if institution and institution not in seen:
                seen.append(institution)
        institutions = seen[:8]
    internal_heat = _safe_float(topic.get("internal_heat_score"), 5, 0, 10)
    video_score = _safe_float(topic.get("video_priority_score"), internal_heat, 0, 10)
    external_heat = topic.get("external_heat_score")
    external_heat_score = None if external_heat in (None, "") else _safe_float(external_heat, 0, 0, 10)
    final_score = internal_heat if external_heat_score is None else (internal_heat * 0.6 + external_heat_score * 0.4)
    return {
        "topic_id": topic_id,
        "topic_name": _trim(topic.get("topic_name") or f"主题 {index}", 120),
        "topic_category": _trim(topic.get("topic_category") or "", 120),
        "reports": reports,
        "report_count": _safe_int(topic.get("report_count"), len(reports), 0, 1000),
        "institutions": institutions,
        "topic_summary": _trim(topic.get("topic_summary") or "", 1200),
        "common_views": _safe_list(topic.get("common_views"), 8),
        "conflicting_views": _safe_list(topic.get("conflicting_views"), 6),
        "representative_reports": _safe_list(topic.get("representative_reports") or reports[:3], 5),
        "internal_heat_score": round(internal_heat, 2),
        "external_heat_score": external_heat_score,
        "final_score": round(final_score, 2),
        "video_priority_score": round(video_score, 2),
        "recommended_video_angle": _trim(topic.get("recommended_video_angle") or "", 800),
        "recommended_title": _trim(topic.get("recommended_title") or topic.get("topic_name") or "", 180),
        "why_it_is_worth_making": _trim(topic.get("why_it_is_worth_making") or topic.get("reason") or "", 1000),
        "search_keywords": _safe_list(topic.get("search_keywords"), 8),
        "search_status": topic.get("search_status") or "not_enabled",
        "why_now": topic.get("why_now") or "当前未启用实时搜索，仅基于上传研报内部热度排序。",
    }


def _rank_topics(topics):
    ranked = sorted(topics, key=lambda item: float(item.get("final_score") or 0), reverse=True)
    result = []
    for rank, topic in enumerate(ranked, start=1):
        result.append({
            "rank": rank,
            "topic_id": topic.get("topic_id"),
            "topic_name": topic.get("topic_name"),
            "report_count": topic.get("report_count"),
            "institutions": topic.get("institutions") or [],
            "internal_heat_score": topic.get("internal_heat_score"),
            "external_heat_score": topic.get("external_heat_score"),
            "final_score": topic.get("final_score"),
            "recommendation": "优先制作" if rank <= 3 else "备选主题",
            "recommended_title": topic.get("recommended_title"),
            "reason": topic.get("why_it_is_worth_making") or topic.get("topic_summary") or "",
        })
    return result


def generate_topic_clusters(task: ReportVideoTask, report_cards):
    report_cards = [card for card in (report_cards or []) if isinstance(card, dict)]
    if not report_cards:
        raise ValueError("请先生成研报快速摘要卡")

    compact_cards = []
    for card in report_cards:
        compact_cards.append({
            key: card.get(key)
            for key in [
                "report_id", "file_name", "report_title", "institution", "report_date",
                "primary_theme", "secondary_themes", "theme_tags", "companies", "industries",
                "assets", "one_sentence_summary", "core_view", "key_points", "why_it_matters",
                "suitable_for_video", "video_value_score", "recommended_usage",
            ]
        })

    system_prompt, user_prompt = render_prompt(
        "topic_clustering",
        {"cards_json": json.dumps(compact_cards, ensure_ascii=False)},
    )

    provider_error = None
    try:
        payload = _deepseek_json(system_prompt, user_prompt, temperature=0.18, timeout=120)
    except Exception as exc:
        provider_error = str(exc)
        logger.warning("Topic clustering DeepSeek failed for task %s: %s", task.id, provider_error)
        payload = None
    if not isinstance(payload, dict) or not isinstance(payload.get("topics"), list):
        payload = _fallback_topics(report_cards)
        if provider_error:
            payload["provider_error"] = provider_error

    report_cards_by_id = {card.get("report_id"): card for card in report_cards}
    topics = [
        _normalize_topic(topic, index, report_cards_by_id)
        for index, topic in enumerate((payload.get("topics") or [])[:10], start=1)
        if isinstance(topic, dict)
    ]
    ranked_topics = _rank_topics(topics)
    return {
        "provider": payload.get("provider") or "deepseek",
        "provider_error": payload.get("provider_error"),
        "topics": topics,
        "ranked_topics": ranked_topics,
        "external_heat": {
            "search_status": "not_enabled",
            "external_heat_score": None,
            "why_now": "当前未启用实时搜索，仅基于上传研报内部热度排序。",
        },
        "generated_at": datetime.utcnow().isoformat(),
    }


def _topic_reports(topic, report_cards):
    wanted = set(topic.get("reports") or [])
    if not wanted:
        return report_cards[:6]
    return [card for card in report_cards if card.get("report_id") in wanted]


def _topic_source_institutions(report_cards):
    institutions = []
    for card in report_cards:
        institution = str(card.get("institution") or "").strip()
        if institution and institution not in institutions:
            institutions.append(institution)
    return institutions


def _build_topic_opening_section(topic, report_cards, payload, title, existing_sections):
    institutions = _topic_source_institutions(report_cards)
    report_count = len(report_cards)
    institution_summary = "、".join(institutions[:4]) or "多家机构"
    topic_name = topic.get("topic_name") or title
    common_views = _safe_list(topic.get("common_views"), 3)
    section_titles = [
        section.get("section_title")
        for section in existing_sections[:4]
        if section.get("section_title")
    ]
    opening_text = _trim(
        payload.get("opening_text")
        or (
            f"今天这批研报里，一条共同主线正在变得更清楚：{topic_name}。"
            f"这不是单篇观点，而是{institution_summary}等机构在{report_count}份研报中共同指向的方向。"
            f"接下来拆它为什么现在升温。"
        ),
        900,
    )
    if len(opening_text) < 48:
        opening_text = _trim(
            f"{opening_text} 这不是单篇报告的孤立观点，而是{institution_summary}等机构在{report_count}份研报中反复指向的主题主线。"
            f"接下来先看为什么现在升温。",
            900,
        )
    opening_text = _trim(opening_text, 220)

    bullets = []
    if institutions:
        bullets.append(f"{len(institutions)}家机构共同关注")
    bullets.append(f"{report_count}份研报联合支撑")
    if common_views:
        bullets.extend(common_views[:2])
    elif section_titles:
        bullets.extend(section_titles[:2])

    return {
        "section_id": "S000",
        "section_index": 0,
        "section_title": _trim(topic_name, 120),
        "section_type": "topic_opening",
        "voice_text": opening_text,
        "visual_explanation": _trim(topic.get("topic_summary") or opening_text, 500),
        "bullet_points": _safe_list(bullets, 3),
        "focus_node_id": "node_overview",
        "source_report_ids": topic.get("reports") or [card.get("report_id") for card in report_cards[:6]],
        "source_page": None,
        "source_evidence": _trim(topic.get("why_it_is_worth_making") or topic.get("recommended_video_angle") or "", 500),
        "estimated_duration_sec": 8,
    }


def _ensure_topic_opening_section(topic, report_cards, payload, title, sections):
    if not sections:
        return [_build_topic_opening_section(topic, report_cards, payload, title, [])]
    first_type = str(sections[0].get("section_type") or "").lower()
    first_title = str(sections[0].get("section_title") or "")
    has_opening = first_type in {"opening", "topic_opening"} or "开场" in first_title
    if has_opening:
        sections[0]["section_id"] = sections[0].get("section_id") or "S000"
        sections[0]["section_index"] = 0
        sections[0]["section_type"] = "topic_opening"
        sections[0]["focus_node_id"] = sections[0].get("focus_node_id") or "node_overview"
        sections[0]["estimated_duration_sec"] = _safe_int(sections[0].get("estimated_duration_sec"), 8, 5, 10)
        if sections[0].get("voice_text"):
            sections[0]["voice_text"] = _trim(sections[0].get("voice_text"), 220)
        return sections
    return [_build_topic_opening_section(topic, report_cards, payload, title, sections), *sections]


def _normalize_topic_script(topic, report_cards, payload):
    payload = payload if isinstance(payload, dict) else {}
    topic_id = _trim(payload.get("topic_id") or topic.get("topic_id") or "T001", 32)
    title = _trim(payload.get("video_title") or topic.get("recommended_title") or topic.get("topic_name") or "主题研报解读", 180)
    sections = []
    raw_sections = payload.get("sections") if isinstance(payload.get("sections"), list) else []
    if not raw_sections:
        views = topic.get("common_views") or []
        for index, view in enumerate((views or [topic.get("topic_summary") or title])[:5], start=1):
            sections.append({
                "section_id": f"S{index:03d}",
                "section_index": index,
                "section_title": _trim(view, 80),
                "section_type": "core_view" if index == 1 else "logic",
                "voice_text": _trim(f"{view}。{topic.get('topic_summary') or ''}", 600),
                "visual_explanation": _trim(topic.get("topic_summary") or view, 300),
                "bullet_points": _safe_list([view], 3),
                "focus_node_id": f"node_{index:03d}",
                "source_report_ids": topic.get("reports") or [card.get("report_id") for card in report_cards[:3]],
                "source_page": None,
                "source_evidence": "",
                "estimated_duration_sec": 30,
            })
    else:
        for index, section in enumerate(raw_sections[:6], start=1):
            if not isinstance(section, dict):
                continue
            focus_node_id = _safe_node_id(section.get("focus_node_id"), f"node_{index:03d}")
            sections.append({
                "section_id": _trim(section.get("section_id") or f"S{index:03d}", 32),
                "section_index": _safe_int(section.get("section_index"), index, 1, 100),
                "section_title": _trim(section.get("section_title") or f"要点 {index}", 120),
                "section_type": _trim(section.get("section_type") or "core_view", 40),
                "voice_text": _trim(section.get("voice_text") or "", 1200),
                "visual_explanation": _trim(section.get("visual_explanation") or section.get("voice_text") or "", 500),
                "bullet_points": _safe_list(section.get("bullet_points"), 4),
                "focus_node_id": focus_node_id,
                "source_report_ids": _safe_list(section.get("source_report_ids") or topic.get("reports"), 6),
                "source_page": section.get("source_page"),
                "source_evidence": _trim(section.get("source_evidence") or "", 500),
                "estimated_duration_sec": _safe_int(section.get("estimated_duration_sec"), 30, 5, 600),
            })

    sections = _ensure_topic_opening_section(topic, report_cards, payload, title, sections)
    for index, section in enumerate(sections):
        section["section_index"] = index
        if index > 0 and section.get("section_id") == "S000":
            section["section_id"] = f"S{index:03d}"

    mindmap = payload.get("theme_mindmap") if isinstance(payload.get("theme_mindmap"), dict) else {}
    raw_nodes = mindmap.get("nodes") if isinstance(mindmap.get("nodes"), list) else []
    if not raw_nodes:
        raw_nodes = [{"id": "root", "label": title[:40], "type": "center", "children": [item["focus_node_id"] for item in sections]}]
        raw_nodes.extend({
            "id": section["focus_node_id"],
            "label": section["section_title"][:40],
            "type": "section",
            "children": [],
        } for section in sections)
    nodes = []
    for index, node in enumerate(raw_nodes[:24]):
        if not isinstance(node, dict):
            continue
        node_id = _safe_node_id(node.get("id"), "root" if index == 0 else f"node_{index:03d}")
        nodes.append({
            "id": node_id,
            "label": _trim(node.get("label") or node.get("title") or node_id, 80),
            "type": _trim(node.get("type") or ("center" if node_id == "root" else "section"), 40),
            "children": [_safe_node_id(child, f"node_{i + 1:03d}") for i, child in enumerate(node.get("children") or [])][:12],
        })

    node_ids = {node["id"] for node in nodes}
    focus_ids = [section["focus_node_id"] for section in sections if section.get("focus_node_id")]
    if "root" not in node_ids:
        nodes.insert(0, {"id": "root", "label": title[:40], "type": "center", "children": focus_ids[:12]})
        node_ids.add("root")
    if "node_overview" not in node_ids:
        nodes.append({"id": "node_overview", "label": "主题总开场", "type": "overview", "children": focus_ids[1:4]})
        node_ids.add("node_overview")
    for node in nodes:
        if node["id"] == "root":
            children = [_safe_node_id(child, f"node_{i + 1:03d}") for i, child in enumerate(node.get("children") or [])]
            merged = []
            for child in ["node_overview", *focus_ids]:
                if child and child not in merged:
                    merged.append(child)
            for child in children:
                if child and child not in merged:
                    merged.append(child)
            node["children"] = merged[:12]
            break
    for section in sections:
        focus_id = section.get("focus_node_id")
        if focus_id and focus_id not in node_ids:
            nodes.append({
                "id": focus_id,
                "label": _trim(section.get("section_title") or focus_id, 80),
                "type": "section",
                "children": [],
            })
            node_ids.add(focus_id)

    source_institutions = _topic_source_institutions(report_cards)
    theme_flow = [
        {
            "id": section.get("focus_node_id") or f"node_{index:03d}",
            "label": _trim(section.get("section_title") or f"章节 {index}", 40),
            "section_id": section.get("section_id") or f"S{index:03d}",
            "section_type": section.get("section_type") or "topic_section",
        }
        for index, section in enumerate(sections)
    ]

    return {
        "topic_id": topic_id,
        "video_title": title,
        "video_subtitle": _trim(payload.get("video_subtitle") or topic.get("topic_summary") or "", 220),
        "opening_text": _trim(payload.get("opening_text") or f"今天这批研报里，最值得注意的一条主线是：{topic.get('topic_name') or title}。", 800),
        "topic_core_view": _trim(payload.get("topic_core_view") or topic.get("topic_summary") or "", 1200),
        "source_card": payload.get("source_card") if isinstance(payload.get("source_card"), dict) else {
            "institution_summary": "、".join(source_institutions[:5]),
            "report_count": len(report_cards),
            "main_institutions": source_institutions[:8],
            "topic_summary": topic.get("topic_summary") or "",
            "focus_points": topic.get("common_views") or [],
        },
        "theme_mindmap": {
            "center": _trim(mindmap.get("center") or title, 255),
            "nodes": nodes,
        },
        "theme_flow": theme_flow,
        "sections": sections,
        "closing_text": _trim(payload.get("closing_text") or "以上就是这条研报主线的核心信息。", 500),
        "provider": payload.get("provider") or "local_fallback",
    }


def generate_topic_video_script(task: ReportVideoTask, topic, report_cards):
    topic_reports = _topic_reports(topic, report_cards)
    if not topic_reports:
        raise ValueError("该主题下没有可引用的研报摘要")

    external_heat = {
        "external_heat_score": None,
        "search_status": "not_enabled",
        "why_now": "当前未启用实时搜索，仅基于上传研报内部热度排序。",
    }
    system_prompt, user_prompt = render_prompt(
        "topic_video_script",
        {
            "topic_id": topic.get("topic_id"),
            "topic_json": json.dumps(topic, ensure_ascii=False),
            "topic_reports_json": json.dumps(topic_reports, ensure_ascii=False),
            "external_heat_json": json.dumps(external_heat, ensure_ascii=False),
        },
    )

    provider_error = None
    try:
        payload = _deepseek_json(system_prompt, user_prompt, temperature=0.2, timeout=120)
    except Exception as exc:
        provider_error = str(exc)
        logger.warning("Topic video script DeepSeek failed for task %s topic %s: %s", task.id, topic.get("topic_id"), provider_error)
        payload = None
    script = _normalize_topic_script(topic, topic_reports, payload)
    if provider_error and script.get("provider") == "local_fallback":
        script["provider_error"] = provider_error
    script["generated_at"] = datetime.utcnow().isoformat()
    return script
