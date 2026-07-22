import json
import hashlib
import os
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from PIL import Image, ImageDraw, ImageFilter, ImageFont

try:
    import fitz  # type: ignore
except Exception:  # pragma: no cover - optional at import time
    fitz = None

FEATURE_NAME = "report_visual_plan"
QUEUE_NAME = "report_visual_plan"
SCHEMA_VERSION = "20260627_visual_plan_v2"
STORYBOARD_SCHEMA_VERSION = "20260721_storyboard_v2_title_scene"
PROJECT_ROOT = Path(__file__).resolve().parents[3]
OUTPUT_ROOT = PROJECT_ROOT / "data" / "report_visual_plans"


def storyboard_prompt(material: Dict[str, Any]) -> str:
    script = _script_from_material(material)
    if not script:
        raise ValueError("基础文案为空，无法生成分镜")
    return (
        "请按照这个 Gem 已配置的规则，把下面这篇完整口播文案逐段转换为分镜画面提示词。"
        "在正文分镜之前，必须先单独输出一个‘标题镜头’，标题镜头只表现研报标题，"
        "并且必须包含一条可直接用于生成图片的完整提示词；不得省略标题镜头。"
        "必须覆盖原文全部内容，保持原文顺序，不得省略、概括或改写原文。"
        "每个分镜同时给出对应的原文片段和可直接用于生成图片的提示词。"
        "提示词的语言、格式和措辞必须遵循这个 Gem 的既有规则；如果 Gem 默认输出英文，"
        "必须保留英文，禁止翻译或改写。\n\n"
        f"标题：{_material_title(material)}\n\n完整口播文案：\n{script}"
    )


def normalize_storyboard_response(material: Dict[str, Any], gemini_text: str) -> Dict[str, Any]:
    script = _script_from_material(material)
    if not script:
        raise ValueError("基础文案为空，无法梳理分镜")
    if not str(gemini_text or "").strip():
        raise ValueError("分镜 Gem 未返回内容")
    payload = _deepseek_json(
        "你是分镜数据整理器，只输出严格 JSON。不得删减、概括、改写输入的原始口播文案。",
        f"""
把“分镜 Gem 返回值”整理成有序分镜列表。

硬性规则：
1. segments 必须覆盖“完整原始口播文案”的每一个非空字符，并保持顺序；
2. 每个 script_text 必须逐字复制原文中的连续片段，所有 script_text 拼接后必须等于完整原文（允许分段边界处空白差异）；
3. 不得把提示词、解释或新增内容混入 script_text；
4. visual_prompt 只能从 Gem 返回值中逐字复制，只允许去掉字段标签和调整首尾空白；
5. 禁止翻译、润色、缩写、扩写或重写 visual_prompt，必须保留 Gem 原有语言和措辞；
6. title_scene 必须提取 Gem 为研报标题生成的标题镜头提示词，不得丢弃；
7. segments 分镜数量以完整表达为准，不得为了减少数量而遗漏文案。

只输出：
{{"title_scene":{{"scene_title":"标题镜头","visual_prompt":"从 Gem 返回值逐字复制的标题提示词","estimated_seconds":3}},"segments":[{{"scene_title":"...","script_text":"原文连续片段","visual_prompt":"从 Gem 返回值逐字复制的原提示词","estimated_seconds":6}}]}}

完整原始口播文案：
{script}

分镜 Gem 返回值：
{str(gemini_text).strip()}
""".strip(),
        temperature=0.08,
        timeout=int(os.getenv("REPORT_VISUAL_PLAN_NORMALIZER_TIMEOUT_SECONDS", "150")),
        model=_storyboard_model_name(),
    )
    raw_segments = payload.get("segments") if isinstance(payload, dict) else None
    if not isinstance(raw_segments, list) or not raw_segments:
        raise ValueError("DS 未返回有效的 segments 列表")
    raw_title_scene = payload.get("title_scene") if isinstance(payload, dict) else None
    if not isinstance(raw_title_scene, dict):
        raise ValueError("DS 未返回标题镜头 title_scene")
    title_prompt = str(raw_title_scene.get("visual_prompt") or "").strip()
    if not title_prompt:
        raise ValueError("DS 未返回标题镜头提示词")
    exact_title_prompt, prompt_offset = _extract_exact_gemini_prompt(str(gemini_text), title_prompt)
    title_scene = {
        "sequence": 0,
        "scene_title": _trim(raw_title_scene.get("scene_title") or "标题镜头", 120),
        "script_text": _material_title(material),
        "visual_prompt": exact_title_prompt,
        "estimated_seconds": _safe_seconds(raw_title_scene.get("estimated_seconds")) or 3.0,
    }
    segments = _align_storyboard_segments(script, raw_segments, str(gemini_text), prompt_offset=prompt_offset)
    return {
        "schema_version": STORYBOARD_SCHEMA_VERSION,
        "source_script_hash": hashlib.sha256(script.encode("utf-8")).hexdigest(),
        "source_script_chars": len(script),
        "covered_script_chars": sum(len(item["script_text"]) for item in segments),
        "segment_count": len(segments) + 1,
        "provider": payload.get("provider") or "deepseek",
        "model": payload.get("model") or _model_name(),
        "title_scene": title_scene,
        "segments": segments,
    }


def _align_storyboard_segments(
    script: str,
    raw_segments: List[Dict[str, Any]],
    gemini_text: str,
    *,
    prompt_offset: int = 0,
) -> List[Dict[str, Any]]:
    compact_source, source_positions = _compact_with_positions(script)
    compact_gemini, gemini_positions = _compact_with_positions(gemini_text)
    compact_parts = []
    cleaned = []
    exact_prompts = []
    for item in raw_segments:
        if not isinstance(item, dict):
            continue
        part = str(item.get("script_text") or "")
        compact_part, _ = _compact_with_positions(part)
        prompt = str(item.get("visual_prompt") or "").strip()
        if compact_part and prompt:
            compact_prompt, _ = _compact_with_positions(prompt)
            prompt_start = compact_gemini.find(compact_prompt, prompt_offset)
            if prompt_start < 0:
                raise ValueError("DS 改写或翻译了 Gem 提示词，已拒绝保存")
            prompt_end = prompt_start + len(compact_prompt)
            exact_start = gemini_positions[prompt_start]
            exact_end = gemini_positions[prompt_end - 1] + 1
            compact_parts.append(compact_part)
            cleaned.append(item)
            exact_prompts.append(gemini_text[exact_start:exact_end])
            prompt_offset = prompt_end
    if not cleaned or "".join(compact_parts) != compact_source:
        raise ValueError("DS 分镜未完整覆盖原始文案，已拒绝保存不完整结果")

    aligned = []
    offset = 0
    for index, (item, compact_part, exact_prompt) in enumerate(
        zip(cleaned, compact_parts, exact_prompts),
        start=1,
    ):
        start_compact = offset
        offset += len(compact_part)
        start = source_positions[start_compact]
        end = source_positions[offset - 1] + 1
        if index < len(cleaned):
            next_start = source_positions[offset]
            end = next_start
        else:
            end = len(script)
        aligned.append({
            "sequence": index,
            "scene_title": _trim(item.get("scene_title") or f"分镜 {index}", 120),
            "script_text": script[start:end],
            "visual_prompt": exact_prompt,
            "estimated_seconds": _safe_seconds(item.get("estimated_seconds")),
        })
    if "".join(item["script_text"] for item in aligned) != script:
        raise ValueError("分镜边界校验失败，已拒绝保存")
    return aligned


def _extract_exact_gemini_prompt(gemini_text: str, prompt: str, offset: int = 0) -> Tuple[str, int]:
    compact_gemini, gemini_positions = _compact_with_positions(gemini_text)
    compact_prompt, _ = _compact_with_positions(prompt)
    prompt_start = compact_gemini.find(compact_prompt, max(0, int(offset or 0)))
    if prompt_start < 0:
        raise ValueError("DS 改写或翻译了 Gem 提示词，已拒绝保存")
    prompt_end = prompt_start + len(compact_prompt)
    exact_start = gemini_positions[prompt_start]
    exact_end = gemini_positions[prompt_end - 1] + 1
    return gemini_text[exact_start:exact_end], prompt_end


def _compact_with_positions(text: str) -> Tuple[str, List[int]]:
    chars = []
    positions = []
    for index, char in enumerate(str(text or "")):
        if not char.isspace():
            chars.append(char)
            positions.append(index)
    return "".join(chars), positions


def _safe_seconds(value: Any) -> Optional[float]:
    try:
        return max(1.0, min(30.0, float(value)))
    except (TypeError, ValueError):
        return None


def provider_status() -> Dict[str, Any]:
    api_key = _api_key()
    return {
        "id": FEATURE_NAME,
        "name": "研报视觉编排",
        "schema_version": SCHEMA_VERSION,
        "configured": bool(api_key),
        "provider": "deepseek" if api_key else "local_fallback",
        "model": _model_name() if api_key else None,
        "base_url": _base_url(),
    }


def build_visual_plan_preview(
    material: Dict[str, Any],
    *,
    pdf_path: Optional[str] = None,
    job_id: Optional[str] = None,
    output_root: Optional[Path] = None,
) -> Dict[str, Any]:
    job_id = job_id or uuid.uuid4().hex
    output_dir = Path(output_root or OUTPUT_ROOT) / job_id
    output_dir.mkdir(parents=True, exist_ok=True)

    plan_payload = generate_visual_plan(material)
    plan = normalize_visual_plan(plan_payload, material)
    assets = render_visual_plan_previews(plan, material, pdf_path=pdf_path, job_id=job_id, output_root=output_root)
    return {
        "schema_version": SCHEMA_VERSION,
        "visual_plan": plan,
        "preview_assets": assets,
        "provider": plan.get("provider") or plan_payload.get("provider") or "local_fallback",
        "provider_error": plan.get("provider_error") or plan_payload.get("provider_error"),
        "generated_at": datetime.utcnow().isoformat() + "Z",
    }


def generate_visual_plan(material: Dict[str, Any]) -> Dict[str, Any]:
    script = _script_from_material(material)
    if not script:
        raise ValueError("基础文案为空，无法生成 visual_plan")

    prompt = _build_user_prompt(material, script)
    payload: Optional[Dict[str, Any]] = None
    provider_error = None
    if _api_key():
        try:
            payload = _deepseek_json(
                _system_prompt(),
                prompt,
                temperature=0.16,
                timeout=int(os.getenv("REPORT_VISUAL_PLAN_TIMEOUT_SECONDS", "90")),
            )
        except Exception as exc:
            provider_error = str(exc)

    if not isinstance(payload, dict):
        payload = _fallback_visual_plan(material, script)
        if provider_error:
            payload["provider_error"] = provider_error
    return payload


def normalize_visual_plan(payload: Dict[str, Any], material: Dict[str, Any]) -> Dict[str, Any]:
    payload = payload if isinstance(payload, dict) else {}
    fallback = _fallback_visual_plan(material, _script_from_material(material))
    raw_cover = payload.get("opening_cover") if isinstance(payload.get("opening_cover"), dict) else {}
    legacy_title = raw_cover.get("title") or _pick(payload, "cover", "title")
    source_title = _clean_title(
        raw_cover.get("source_title")
        or _pick(payload, "cover", "source_title")
        or fallback["opening_cover"]["source_title"]
        or _material_title(material),
        40,
    )
    subtitle = _trim(
        raw_cover.get("subtitle")
        or _pick(payload, "cover", "subtitle")
        or _first_non_empty_sentence(_script_from_material(material))
        or fallback["opening_cover"]["subtitle"],
        42,
    )
    hook_title = _trim(
        raw_cover.get("hook_title")
        or _pick(payload, "cover", "hook_title")
        or fallback["opening_cover"]["hook_title"]
        or _derive_question(source_title, subtitle),
        34,
    )
    cover_title = _clean_title(
        raw_cover.get("cover_title")
        or _pick(payload, "cover", "cover_title")
        or legacy_title
        or fallback["opening_cover"]["cover_title"]
        or _derive_cover_title(source_title, hook_title),
        42,
    )
    highlight_terms = _safe_text_list(raw_cover.get("highlight_terms"), 4, 10)
    if not highlight_terms:
        highlight_terms = _derive_highlight_terms(cover_title, source_title, hook_title, subtitle, _script_from_material(material))

    raw_cards = payload.get("logic_cards") if isinstance(payload.get("logic_cards"), list) else []
    if not raw_cards:
        raw_cards = fallback.get("logic_cards") or []

    cards = []
    for index, card in enumerate(raw_cards[:2], start=1):
        if not isinstance(card, dict):
            continue
        normalized = _normalize_logic_card(card, index, material)
        if normalized:
            cards.append(normalized)
    if not cards:
        cards = [_normalize_logic_card(fallback["logic_cards"][0], 1, material)]

    body = payload.get("body_background") if isinstance(payload.get("body_background"), dict) else {}
    return {
        "schema_version": SCHEMA_VERSION,
        "opening_cover": {
            "title": cover_title,
            "source_title": source_title,
            "hook_title": hook_title,
            "cover_title": cover_title,
            "highlight_terms": highlight_terms,
            "subtitle": subtitle,
            "source_line": _normalize_source_line(raw_cover.get("source_line"), material),
        },
        "logic_cards": cards,
        "body_background": {
            "mode": _trim(body.get("mode") or "clean_subtitle_safe", 40),
            "title_short": _trim(body.get("title_short") or cards[0]["core_question"] or hook_title or cover_title, 28),
        },
        "provider": payload.get("provider") or "local_fallback",
        "provider_error": payload.get("provider_error"),
    }


def render_visual_plan_previews(
    plan: Dict[str, Any],
    material: Dict[str, Any],
    *,
    pdf_path: Optional[str] = None,
    job_id: Optional[str] = None,
    output_root: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    job_id = job_id or uuid.uuid4().hex
    output_dir = Path(output_root or OUTPUT_ROOT) / job_id
    output_dir.mkdir(parents=True, exist_ok=True)

    pdf_preview_path = output_dir / "pdf_preview.jpg"
    _render_pdf_preview(pdf_path, pdf_preview_path, material)

    opening_path = output_dir / "opening_cover.jpg"
    _render_opening_cover(plan, material, pdf_preview_path, opening_path)

    logic_path = output_dir / "logic_card_01.jpg"
    _render_logic_card(plan, material, pdf_preview_path, logic_path)

    return [
        _asset_payload("opening_cover", opening_path),
        _asset_payload("logic_card", logic_path),
        _asset_payload("pdf_preview", pdf_preview_path),
    ]


def _system_prompt() -> str:
    return (
        "你是财经短视频的视觉总编辑。你只输出严格 JSON，不要 Markdown，不要解释。"
        "你的任务是把已经解析好的单篇研报基础文案转成 visual_plan。"
        "你不能重新解析 PDF，不能编造基础文案中没有的数字、结论、机构观点。"
        "每个逻辑卡和步骤都要能从输入文案中找到依据。"
    )


def _build_user_prompt(material: Dict[str, Any], script: str) -> str:
    meta = {
        "title": material.get("title"),
        "pdf": material.get("pdf") or {},
        "script_chars": len(script),
        "structured_result": _compact_structured(material.get("structured_result")),
    }
    return f"""
请基于下面的“基础文案物料”生成一个用于财经竖屏视频的 visual_plan。

输出要求：
1. 只输出严格 JSON；
2. 不要生成图片，不要写 HTML；
3. opening_cover 用于 2-4 秒开场封面；
4. logic_cards 用于 6-10 秒中场图解卡；
5. 最多 1 张 logic_card，步骤 3 个，flow_nodes 4-5 个；
6. highlight_terms 只能选标题或文案中真实出现的关键词/数字；
7. source_evidence 必须摘录或概括自基础文案，不能凭空补充。
8. opening_cover 要区分三类标题：
   - source_title：研报原始主题/中性主题，不要写成问题；
   - hook_title：视频钩子，可写成问题或一句观点；
   - cover_title：开场封面主标题，必须保留研报主题，不要被某一张逻辑卡的问题替代。
9. opening_cover.source_line 只能写“机构/栏目 | 日期”这种短来源，不要写观点句或证据句。

JSON 格式：
{{
  "opening_cover": {{
    "source_title": "...",
    "hook_title": "...",
    "cover_title": "...",
    "highlight_terms": ["...", "..."],
    "subtitle": "...",
    "source_line": "..."
  }},
  "logic_cards": [
    {{
      "card_id": "logic_001",
      "core_question": "...",
      "subtitle": "...",
      "steps": [
        {{"title": "...", "desc": "...", "source_evidence": "..."}},
        {{"title": "...", "desc": "...", "source_evidence": "..."}},
        {{"title": "...", "desc": "...", "source_evidence": "..."}}
      ],
      "flow_title": "...",
      "flow_nodes": ["...", "...", "...", "..."],
      "host_tip": "...",
      "one_sentence": "...",
      "source_evidence": "..."
    }}
  ],
  "body_background": {{
    "mode": "clean_subtitle_safe",
    "title_short": "..."
  }}
}}

元数据：
{json.dumps(meta, ensure_ascii=False)}

基础文案：
{_trim(script, 12000)}
""".strip()


def _deepseek_json(system_prompt: str, user_prompt: str, *, temperature: float, timeout: int, model: Optional[str] = None) -> Dict[str, Any]:
    model_name = model or _model_name()
    response = requests.post(
        f"{_base_url().rstrip('/')}/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {_api_key()}",
            "Content-Type": "application/json",
        },
        json={
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": temperature,
        },
        timeout=timeout,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    payload = _json_from_text(content)
    payload["provider"] = "deepseek"
    payload["model"] = model_name
    return payload


def _fallback_visual_plan(material: Dict[str, Any], script: str) -> Dict[str, Any]:
    title = _material_title(material) or "研报核心观点解读"
    sentences = _sentences(script)
    first = sentences[0] if sentences else title
    second = sentences[1] if len(sentences) > 1 else first
    third = sentences[2] if len(sentences) > 2 else second
    source_title = _clean_title(title, 40)
    question = _derive_question(source_title, first)
    highlights = _derive_highlight_terms(source_title, first, script)
    hook_title = _trim(question, 34)
    cover_title = _derive_cover_title(source_title, hook_title)
    flow_nodes = _derive_flow_nodes(script, title)
    steps = [
        {"title": _trim(_keyword_title(first, "核心主线"), 18), "desc": _trim(first, 44), "source_evidence": _trim(first, 90)},
        {"title": _trim(_keyword_title(second, "传导机制"), 18), "desc": _trim(second, 44), "source_evidence": _trim(second, 90)},
        {"title": _trim(_keyword_title(third, "投资含义"), 18), "desc": _trim(third, 44), "source_evidence": _trim(third, 90)},
    ]
    return {
        "opening_cover": {
            "title": cover_title,
            "source_title": source_title,
            "hook_title": hook_title,
            "cover_title": cover_title,
            "highlight_terms": highlights,
            "subtitle": _trim(first, 42),
            "source_line": _source_line(material),
        },
        "logic_cards": [{
            "card_id": "logic_001",
            "core_question": question,
            "subtitle": _trim(first, 42),
            "steps": steps,
            "flow_title": "关键逻辑链路（示意）",
            "flow_nodes": flow_nodes,
            "host_tip": _trim(f"这段逻辑的关键不是单点判断，而是看清 {flow_nodes[0]} 如何传导到 {flow_nodes[-1]}。", 70),
            "one_sentence": _trim(first, 80),
            "source_evidence": _trim(" ".join(sentences[:3]), 180),
        }],
        "body_background": {"mode": "clean_subtitle_safe", "title_short": _trim(question, 28)},
        "provider": "local_fallback",
    }


def _normalize_logic_card(card: Dict[str, Any], index: int, material: Dict[str, Any]) -> Dict[str, Any]:
    script = _script_from_material(material)
    fallback = _fallback_visual_plan(material, script)["logic_cards"][0]
    steps = card.get("steps") if isinstance(card.get("steps"), list) else []
    normalized_steps = []
    for step_index, step in enumerate(steps[:3], start=1):
        if not isinstance(step, dict):
            continue
        title = _trim(step.get("title") or f"步骤 {step_index}", 18)
        desc = _trim(step.get("desc") or step.get("description") or step.get("summary") or "", 44)
        evidence = _trim(step.get("source_evidence") or desc, 120)
        if title and desc:
            normalized_steps.append({"title": title, "desc": desc, "source_evidence": evidence})
    while len(normalized_steps) < 3:
        normalized_steps.append(fallback["steps"][len(normalized_steps)])

    flow_nodes = _safe_text_list(card.get("flow_nodes"), 5, 14)
    if len(flow_nodes) < 4:
        flow_nodes = fallback["flow_nodes"]
    return {
        "card_id": _trim(card.get("card_id") or f"logic_{index:03d}", 32),
        "core_question": _trim(card.get("core_question") or fallback["core_question"], 30),
        "subtitle": _trim(card.get("subtitle") or fallback["subtitle"], 42),
        "steps": normalized_steps[:3],
        "flow_title": _trim(card.get("flow_title") or fallback["flow_title"], 30),
        "flow_nodes": flow_nodes[:5],
        "host_tip": _trim(card.get("host_tip") or fallback["host_tip"], 78),
        "one_sentence": _trim(card.get("one_sentence") or fallback["one_sentence"], 76),
        "source_evidence": _trim(card.get("source_evidence") or fallback["source_evidence"], 180),
    }


def _render_pdf_preview(pdf_path: Optional[str], output_path: Path, material: Dict[str, Any]) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    path = Path(str(pdf_path or ""))
    if fitz is not None and path.exists() and path.is_file():
        try:
            with fitz.open(str(path)) as doc:
                if doc.page_count:
                    pix = doc.load_page(0).get_pixmap(matrix=fitz.Matrix(1.25, 1.25), alpha=False)
                    pix.save(str(output_path))
                    return
        except Exception:
            pass

    img = Image.new("RGB", (900, 520), "#f8fafc")
    draw = ImageDraw.Draw(img)
    font = _font(34)
    small = _font(22)
    draw.rectangle((0, 0, 900, 112), fill="#d3382a")
    draw.text((46, 32), _trim(_source_line(material), 34), fill="white", font=font)
    draw.text((46, 168), _trim(_material_title(material), 42), fill="#0f172a", font=font)
    for y in range(250, 430, 36):
        draw.line((46, y, 820, y), fill="#cbd5e1", width=3)
    draw.text((46, 452), "PDF preview placeholder", fill="#64748b", font=small)
    img.save(output_path, quality=94)


def _render_opening_cover(plan: Dict[str, Any], material: Dict[str, Any], pdf_preview_path: Path, output_path: Path) -> None:
    cover = plan.get("opening_cover") or {}
    card = (plan.get("logic_cards") or [{}])[0]
    width, height = 1080, 1920
    img = Image.new("RGB", (width, height), "#06111f")
    draw = ImageDraw.Draw(img)
    _draw_gradient_background(draw, width, height)

    _paste_image_cover(img, pdf_preview_path, (0, 0, width, 560))
    _draw_top_fade(img, 380, 640)
    draw.line((0, 560, width, 560), fill="#d9b64a", width=2)

    meta_y = 636
    draw.rectangle((92, meta_y - 8, 186, meta_y - 2), fill="#f6d365")
    meta_font = _font(28)
    meta = f"研报播客  |  {cover.get('source_line') or _source_line(material)}"
    draw.text((214, meta_y - 24), _trim(meta, 34), fill="#cfd9e6", font=meta_font)

    source_font = _font(28)
    source = cover.get("source_title") or _material_title(material)
    source_line = _wrap_text(draw, f"研报主题：{source}", source_font, 900, 1)
    draw.text((92, 706), source_line[0] if source_line else "研报主题", fill="#93a4b8", font=source_font)

    title = str(cover.get("cover_title") or cover.get("title") or source or "研报核心观点")
    highlight_terms = cover.get("highlight_terms") or []
    title_font = _font(76 if len(title) <= 24 else 66)
    y = 786
    for line in _wrap_text(draw, title, title_font, 900, 4):
        _draw_highlighted_text(draw, (92, y), line, title_font, set(highlight_terms))
        y += int(title_font.size * 1.18) if hasattr(title_font, "size") else 82

    hook = cover.get("hook_title") or cover.get("subtitle") or ""
    hook_font = _font(36)
    hook_y = min(y + 20, 1136)
    for line in _wrap_text(draw, hook, hook_font, 900, 2):
        draw.text((92, hook_y), line, fill="#e5edf6", font=hook_font)
        hook_y += 50

    subtitle = cover.get("subtitle") or ""
    sub_y = hook_y + 12
    for line in _wrap_text(draw, subtitle, _font(28), 900, 2):
        draw.text((92, sub_y), line, fill="#9fb0c2", font=_font(28))
        sub_y += 38

    teaser_top = max(1180, sub_y + 44)
    _draw_opening_teaser(draw, card, (72, teaser_top, 1008, 1818))
    draw.text((92, 1850), "内容依据来自已生成基础文案，预览图用于验证视觉编排。", fill="#64748b", font=_font(24))

    img = img.filter(ImageFilter.UnsharpMask(radius=1, percent=112, threshold=3))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, quality=95)


def _render_logic_card(plan: Dict[str, Any], material: Dict[str, Any], pdf_preview_path: Path, output_path: Path) -> None:
    width, height = 1080, 1920
    card = (plan.get("logic_cards") or [{}])[0]
    cover = plan.get("opening_cover") or {}
    img = Image.new("RGB", (width, height), "#06111f")
    draw = ImageDraw.Draw(img)
    _draw_gradient_background(draw, width, height)

    panel = (8, 8, width - 8, height - 8)
    draw.rounded_rectangle(panel, radius=28, outline="#31445a", width=3)

    meta_font = _font(28)
    meta_gold = _font(30)
    title_text = card.get("core_question") or cover.get("title") or "核心问题"
    title_font = _font(58 if len(str(title_text)) <= 20 else 50)
    title_gold = _font(50)
    sub_font = _font(34)
    small_font = _font(24)

    draw.text((54, 62), "研报播客", fill="#f6d365", font=meta_gold)
    draw.text((188, 63), f"|  {_trim(_source_line(material), 34)}", fill="#e5e7eb", font=meta_font)
    _paste_image_contain(img, pdf_preview_path, (728, 44, 1038, 214), radius=8)
    draw.line((54, 264, 1026, 264), fill="#23364e", width=2)
    draw.rectangle((54, 316, 142, 322), fill="#f6d365")
    draw.text((54, 362), "本页核心：", fill="#f6d365", font=title_gold)

    y = 438
    for line in _wrap_text(draw, title_text, title_font, 900, 2):
        draw.text((54, y), line, fill="#f8fafc", font=title_font)
        y += int(title_font.size * 1.18) if hasattr(title_font, "size") else 66
    for line in _wrap_text(draw, card.get("subtitle") or cover.get("subtitle") or "", sub_font, 900, 2):
        draw.text((54, y + 12), line, fill="#dbe4ee", font=sub_font)
        y += 46

    left_box = (54, 650, 504, 1346)
    right_box = (528, 650, 1026, 1346)
    _draw_soft_panel(draw, left_box, "#091827", "#7a6421")
    _draw_soft_panel(draw, right_box, "#091827", "#546172")
    _draw_steps(draw, card.get("steps") or [], left_box)
    _draw_flow(draw, card, right_box)

    tip_box = (54, 1388, 1026, 1572)
    _draw_soft_panel(draw, tip_box, "#071f35", "#4d8bc2")
    draw.ellipse((86, 1424, 176, 1514), outline="#d9b64a", width=2)
    draw.text((113, 1450), "MIC", fill="#f6d365", font=_font(22))
    draw.text((208, 1428), "主播提示：", fill="#f6d365", font=_font(32))
    for idx, line in enumerate(_wrap_text(draw, card.get("host_tip") or "", _font(28), 720, 2)):
        draw.text((208, 1480 + idx * 38), line, fill="#f8fafc", font=_font(28))

    one_box = (54, 1612, 1026, 1788)
    _draw_soft_panel(draw, one_box, "#091827", "#2d4157")
    draw.text((208, 1648), "一句话理解", fill="#f6d365", font=_font(30))
    for idx, line in enumerate(_wrap_text(draw, card.get("one_sentence") or "", _font(28), 760, 2)):
        draw.text((208, 1700 + idx * 38), line, fill="#f8fafc", font=_font(28))
    draw.ellipse((86, 1644, 176, 1734), outline="#5d4a14", width=2)
    draw.text((114, 1671), "IDEA", fill="#f6d365", font=_font(18))

    source = f"依据：{_trim(card.get('source_evidence') or _script_from_material(material), 62)}"
    for idx, line in enumerate(_wrap_text(draw, source, small_font, 940, 1)):
        draw.text((66, 1826 + idx * 34), line, fill="#94a3b8", font=small_font)
    draw.text((66, 1876), "详细依据保留在 visual_plan JSON；仅供学习交流，不构成任何投资建议。", fill="#64748b", font=small_font)

    img = img.filter(ImageFilter.UnsharpMask(radius=1, percent=115, threshold=3))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, quality=95)


def _draw_steps(draw: ImageDraw.ImageDraw, steps: List[Dict[str, str]], box: Tuple[int, int, int, int]) -> None:
    x1, y1, x2, y2 = box
    step_h = (y2 - y1) // 3
    title_font = _font(28)
    desc_font = _font(25)
    for index, step in enumerate(steps[:3], start=1):
        top = y1 + (index - 1) * step_h
        if index > 1:
            draw.line((x1, top, x2, top), fill="#2b3a4f", width=1)
            draw.polygon([(x1 + 205, top - 13), (x1 + 232, top - 13), (x1 + 218, top + 12)], fill="#f6d365")
        draw.ellipse((x1 + 24, top + 24, x1 + 80, top + 80), fill="#f6d365")
        draw.text((x1 + 36, top + 37), f"{index:02d}", fill="#111827", font=_font(24))
        title_lines = _wrap_text(draw, step.get("title") or "", title_font, x2 - x1 - 122, 2)
        for line_index, line in enumerate(title_lines):
            draw.text((x1 + 100, top + 30 + line_index * 34), line, fill="#f6d365", font=title_font)
        desc_top = top + 98 + max(0, len(title_lines) - 1) * 22
        for line_index, line in enumerate(_wrap_text(draw, step.get("desc") or "", desc_font, x2 - x1 - 160, 2)):
            draw.text((x1 + 154, desc_top + line_index * 34), line, fill="#e5e7eb", font=desc_font)


def _draw_flow(draw: ImageDraw.ImageDraw, card: Dict[str, Any], box: Tuple[int, int, int, int]) -> None:
    x1, y1, x2, y2 = box
    title_font = _font(30)
    node_font = _font(28)
    draw.text((x1 + 122, y1 + 32), _trim(card.get("flow_title") or "逻辑链路", 20), fill="#f8fafc", font=title_font)
    nodes = [str(item) for item in (card.get("flow_nodes") or []) if str(item).strip()][:5]
    if len(nodes) < 4:
        nodes = ["资金来源", "政策工具", "资本放大", "产业落地"]
    y = y1 + 106
    colors = ["#12375b", "#0d4b46", "#554719", "#27204f", "#12375b"]
    for index, node in enumerate(nodes):
        number_match = re.search(r"\d+(?:\.\d+)?\s*(?:倍|%|％|x)", node, re.I)
        if index == 3 and number_match:
            draw.text((x1 + 186, y + 0), _trim(number_match.group(0), 8), fill="#f6d365", font=_font(58))
            label = re.sub(re.escape(number_match.group(0)), "", node).strip(" ：:-|｜")
            if label:
                label_lines = _wrap_text(draw, label, _font(22), x2 - x1 - 150, 1)
                if label_lines:
                    draw.text((x1 + 160, y + 72), label_lines[0], fill="#9fb5c5", font=_font(22))
            y += 102
        else:
            draw.rounded_rectangle((x1 + 52, y, x2 - 52, y + 76), radius=18, fill=colors[index % len(colors)], outline="#46627d", width=1)
            label_lines = _wrap_text(draw, node, node_font, x2 - x1 - 150, 1)
            draw.text((x1 + 132, y + 23), label_lines[0] if label_lines else _trim(node, 14), fill="#e5eef8", font=node_font)
            draw.ellipse((x1 + 82, y + 20, x1 + 118, y + 56), outline="#d9b64a", width=2)
            y += 96
        if index < len(nodes) - 1:
            draw.line((x1 + 250, y - 10, x1 + 250, y + 22), fill="#9fb5c5", width=3)
            draw.polygon([(x1 + 238, y + 18), (x1 + 262, y + 18), (x1 + 250, y + 38)], fill="#9fb5c5")
            y += 34


def _draw_gradient_background(draw: ImageDraw.ImageDraw, width: int, height: int) -> None:
    top = (7, 24, 39)
    bottom = (3, 10, 20)
    for y in range(height):
        ratio = y / max(height - 1, 1)
        color = tuple(int(top[i] * (1 - ratio) + bottom[i] * ratio) for i in range(3))
        draw.line((0, y, width, y), fill=color)
    for x in range(0, width, 52):
        draw.line((x, 650, x, 1788), fill="#0b2238", width=1)
    for y in range(650, 1788, 52):
        draw.line((42, y, width - 42, y), fill="#0b2238", width=1)


def _draw_soft_panel(draw: ImageDraw.ImageDraw, box: Tuple[int, int, int, int], fill: str, outline: str) -> None:
    x1, y1, x2, y2 = box
    draw.rounded_rectangle((x1 + 8, y1 + 10, x2 + 8, y2 + 10), radius=24, fill=(0, 0, 0, 54))
    draw.rounded_rectangle(box, radius=24, fill=fill, outline=outline, width=1)


def _paste_image_cover(base: Image.Image, path: Path, box: Tuple[int, int, int, int]) -> None:
    try:
        source = Image.open(path).convert("RGB")
    except Exception:
        return
    x1, y1, x2, y2 = box
    target_w, target_h = x2 - x1, y2 - y1
    if not target_w or not target_h:
        return
    scale = max(target_w / source.width, target_h / source.height)
    resized = source.resize((max(1, int(source.width * scale)), max(1, int(source.height * scale))), Image.Resampling.LANCZOS)
    left = max(0, (resized.width - target_w) // 2)
    top = max(0, min((resized.height - target_h) // 3, resized.height - target_h))
    crop = resized.crop((left, top, left + target_w, top + target_h))
    base.paste(crop, (x1, y1))


def _draw_top_fade(base: Image.Image, start_y: int, end_y: int) -> None:
    width, height = base.size
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    for y in range(max(0, start_y), min(height, end_y)):
        ratio = (y - start_y) / max(1, end_y - start_y)
        alpha = int(212 * ratio)
        draw.line((0, y, width, y), fill=(4, 13, 26, alpha))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rectangle((0, end_y, width, height), fill=(4, 13, 26, 212))
    if base.mode == "RGBA":
        base.alpha_composite(overlay)
    else:
        base.paste(Image.alpha_composite(base.convert("RGBA"), overlay).convert("RGB"), (0, 0))


def _draw_highlighted_text(draw: ImageDraw.ImageDraw, pos: Tuple[int, int], text: str, font, highlights: set) -> None:
    x, y = pos
    normalized = str(text or "")
    terms = [term for term in sorted((str(item) for item in highlights if item), key=len, reverse=True) if term in normalized]
    if not terms:
        draw.text((x, y), normalized, fill="#f8fafc", font=font)
        return
    pattern = "|".join(re.escape(term) for term in terms)
    parts = re.split(f"({pattern})", normalized)
    cursor = x
    for part in parts:
        if not part:
            continue
        fill = "#f6d365" if part in terms else "#f8fafc"
        draw.text((cursor, y), part, fill=fill, font=font)
        cursor += draw.textbbox((cursor, y), part, font=font)[2] - cursor


def _draw_opening_teaser(draw: ImageDraw.ImageDraw, card: Dict[str, Any], box: Tuple[int, int, int, int]) -> None:
    x1, y1, x2, y2 = box
    _draw_soft_panel(draw, box, "#071a2a", "#46627d")
    draw.rectangle((x1 + 42, y1 + 48, x1 + 48, y1 + 82), fill="#f6d365")
    draw.text((x1 + 64, y1 + 42), "核心逻辑预告", fill="#f6d365", font=_font(32))

    steps = [step for step in (card.get("steps") or []) if isinstance(step, dict)][:3]
    if not steps:
        steps = [
            {"title": "核心变量", "desc": "抓住研报中的关键变化。"},
            {"title": "传导机制", "desc": "看清变量之间如何传导。"},
            {"title": "投资含义", "desc": "落到产业链与节奏判断。"},
        ]
    col_gap = 24
    col_w = (x2 - x1 - 84 - col_gap * 2) // 3
    top = y1 + 118
    for index, step in enumerate(steps, start=1):
        cx = x1 + 42 + (index - 1) * (col_w + col_gap)
        draw.rounded_rectangle((cx, top, cx + col_w, top + 210), radius=22, fill="#0b2238", outline="#29445d", width=1)
        draw.ellipse((cx + 24, top + 26, cx + 74, top + 76), fill="#f6d365")
        draw.text((cx + 36, top + 38), f"{index}", fill="#101827", font=_font(24))
        draw.text((cx + 90, top + 30), _short_label(step.get("title"), 8), fill="#f8fafc", font=_font(28))
        for line_index, line in enumerate(_wrap_text(draw, step.get("desc") or "", _font(23), col_w - 48, 3)):
            draw.text((cx + 24, top + 98 + line_index * 34), line, fill="#b9c7d6", font=_font(23))

    nodes = [str(item) for item in (card.get("flow_nodes") or []) if str(item).strip()][:4]
    if len(nodes) < 4:
        nodes = ["核心变量", "政策/资本", "需求放大", "产业落地"]
    flow_y = min(y2 - 132, top + 272)
    draw.text((x1 + 42, flow_y), "链路：", fill="#93a4b8", font=_font(26))
    x = x1 + 122
    for index, node in enumerate(nodes):
        label = _trim(node, 6)
        chip_w = 142
        draw.rounded_rectangle((x, flow_y - 8, x + chip_w, flow_y + 42), radius=20, fill="#0c2a46", outline="#315a7c", width=1)
        draw.text((x + 18, flow_y + 4), label, fill="#e5edf6", font=_font(22))
        x += chip_w + 26
        if index < len(nodes) - 1:
            draw.line((x - 17, flow_y + 18, x + 7, flow_y + 18), fill="#f6d365", width=2)
            draw.polygon([(x + 7, flow_y + 18), (x - 4, flow_y + 11), (x - 4, flow_y + 25)], fill="#f6d365")
            x += 26


def _paste_image_contain(base: Image.Image, path: Path, box: Tuple[int, int, int, int], radius: int = 0) -> None:
    try:
        source = Image.open(path).convert("RGB")
    except Exception:
        return
    x1, y1, x2, y2 = box
    target_w, target_h = x2 - x1, y2 - y1
    canvas = Image.new("RGB", (target_w, target_h), "#0f172a")
    source.thumbnail((target_w, target_h), Image.Resampling.LANCZOS)
    canvas.paste(source, ((target_w - source.width) // 2, (target_h - source.height) // 2))
    if radius > 0:
        mask = Image.new("L", (target_w, target_h), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, target_w, target_h), radius=radius, fill=255)
        base.paste(canvas, (x1, y1), mask)
    else:
        base.paste(canvas, (x1, y1))


def _render_simple_vertical_fallback(cover: Dict[str, Any], output_path: Path) -> None:
    img = Image.new("RGB", (1080, 1920), "#06111f")
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, 1080, 520), fill="#f8fafc")
    draw.rectangle((0, 520, 1080, 1920), fill="#06111f")
    draw.rectangle((86, 650, 222, 656), fill="#f6d365")
    y = 710
    for line in _wrap_text(draw, cover.get("title") or "研报核心观点", _font(64), 908, 4):
        draw.text((86, y), line, fill="#f8fafc", font=_font(64))
        y += 78
    for line in _wrap_text(draw, cover.get("subtitle") or "", _font(34), 908, 2):
        draw.text((86, y + 24), line, fill="#cbd5e1", font=_font(34))
        y += 44
    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, quality=95)


def _asset_payload(kind: str, path: Path) -> Dict[str, Any]:
    return {
        "kind": kind,
        "filename": path.name,
        "path": str(path),
        "bytes": path.stat().st_size if path.exists() else 0,
    }


def _api_key() -> str:
    return (os.getenv("REPORT_VISUAL_PLAN_API_KEY") or os.getenv("DEEPSEEK_API_KEY") or "").strip()


def _base_url() -> str:
    return os.getenv("REPORT_VISUAL_PLAN_BASE_URL") or os.getenv("DEEPSEEK_BASE_URL") or "https://api.deepseek.com"


def _model_name() -> str:
    return os.getenv("REPORT_VISUAL_PLAN_MODEL") or os.getenv("DEEPSEEK_MODEL") or "deepseek-v4-flash"


def _storyboard_model_name() -> str:
    return os.getenv("REPORT_VISUAL_PLAN_NORMALIZER_MODEL") or os.getenv("DEEPSEEK_FLASH_MODEL") or "deepseek-v4-flash"


def _json_from_text(value: str) -> Dict[str, Any]:
    text = (value or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match:
        text = match.group(0)
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("LLM 返回的 JSON 根节点不是对象")
    return parsed


def _font(size: int):
    candidates = [
        os.getenv("REPORT_VISUAL_PLAN_FONT_PATH"),
        str(PROJECT_ROOT / "assets" / "fonts" / "SourceHanSansSC-Heavy.otf"),
        str(PROJECT_ROOT / "assets" / "fonts" / "SourceHanSansSC-Bold.otf"),
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/STHeiti Medium.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    ]
    for path in candidates:
        if path and Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _wrap_text(draw: ImageDraw.ImageDraw, text: Any, font, max_width: int, max_lines: int = 4) -> List[str]:
    text = re.sub(r"\s+", " ", str(text or "")).strip()
    if not text:
        return []
    tokens = re.findall(r"[\u4e00-\u9fff]|[A-Za-z0-9.]+|[^\u4e00-\u9fff]", text)
    lines: List[str] = []
    current = ""
    for token in tokens:
        if re.fullmatch(r"[，。！？；：,.!?;:、]", token) and not current and lines:
            lines[-1] = f"{lines[-1]}{token}"
            continue
        candidate = current + token
        if current and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
            lines.append(current.strip())
            current = token
            if len(lines) >= max_lines:
                break
        else:
            current = candidate
    if current.strip() and len(lines) < max_lines:
        lines.append(current.strip())
    if len(lines) >= 2 and re.fullmatch(r"[，。！？；：,.!?;:、]+", lines[-1]):
        lines[-2] = f"{lines[-2]}{lines[-1]}"
        lines.pop()
    if len(lines) == max_lines and len("".join(lines)) < len(text):
        lines[-1] = lines[-1].rstrip("，,。；; ") + "..."
    return lines


def _script_from_material(material: Dict[str, Any]) -> str:
    structured = material.get("structured_result") if isinstance(material.get("structured_result"), dict) else {}
    return str(material.get("final_script_for_tts") or structured.get("final_script_for_tts") or "").strip()


def _material_title(material: Dict[str, Any]) -> str:
    pdf = material.get("pdf") if isinstance(material.get("pdf"), dict) else {}
    structured = material.get("structured_result") if isinstance(material.get("structured_result"), dict) else {}
    for value in (
        structured.get("cover_title"),
        structured.get("title"),
        material.get("title"),
        pdf.get("topic_title"),
        pdf.get("filename"),
    ):
        text = str(value or "").strip()
        if text and "".join(text.split()).lstrip("#") not in {"外资研报", "研报", "PDF", "pdf"}:
            return Path(text).stem
    return "研报核心观点解读"


def _source_line(material: Dict[str, Any]) -> str:
    title = _material_title(material)
    pdf = material.get("pdf") if isinstance(material.get("pdf"), dict) else {}
    source = pdf.get("tab_label") or _institution_from_text(title) or "机构研报"
    date = _date_from_text(pdf.get("filename") or title)
    parts = [str(source)]
    if date:
        parts.append(date)
    return " | ".join(parts)


def _normalize_source_line(value: Any, material: Dict[str, Any]) -> str:
    text = _trim(value, 80)
    if not text:
        return _source_line(material)
    if len(text) > 42 or re.search(r"[。！？!?；;]", text):
        return _source_line(material)
    return text


def _short_label(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if not text:
        return ""
    for separator in ("：", ":", "；", ";", "，", ",", "｜", "|", "—"):
        if separator in text:
            head = text.split(separator, 1)[0].strip()
            if len(head) >= 2:
                return _trim(head, limit)
    return _trim(text, limit)


def _compact_structured(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    keys = ["title", "cover_title", "cover_subtitle", "summary", "key_points", "final_script_for_tts"]
    result = {}
    for key in keys:
        if key in value:
            result[key] = _trim(value.get(key), 1200)
    return result


def _sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[。！？!?；;])\s*|\n+", text or "")
    return [re.sub(r"\s+", " ", part).strip() for part in parts if len(part.strip()) >= 12]


def _first_non_empty_sentence(text: str) -> str:
    sentences = _sentences(text)
    return sentences[0] if sentences else ""


def _derive_question(title: str, sentence: str) -> str:
    base = re.sub(r"^[#\s]+", "", title or sentence or "这份研报的核心逻辑")
    for separator in ("：", ":", "-", "—", "|", "｜"):
        if separator in base:
            parts = [part.strip() for part in base.split(separator) if part.strip()]
            if parts:
                base = parts[-1]
            break
    base = re.split(r"[。！？!?；;]", base, maxsplit=1)[0]
    base = _trim(base, 16)
    if base.endswith(("?", "？")):
        return base
    return f"{base}的核心逻辑是什么？"


def _derive_cover_title(source_title: str, hook_title: str) -> str:
    source = _clean_title(source_title, 42)
    if source and len(source) >= 12:
        return source
    hook = re.sub(r"[？?]$", "", str(hook_title or "")).strip()
    if source and hook and source not in hook:
        return _clean_title(f"{source}：{hook}", 42)
    return _clean_title(source or hook or "研报核心观点解读", 42)


def _derive_highlight_terms(*values: str) -> List[str]:
    text = " ".join(str(item or "") for item in values)
    terms = []
    for pattern in (r"\d+(?:\.\d+)?\s*(?:倍|%|％|GW|GWh|TWh|亿|万亿)", r"AI算力|算力基础设施|资本开支|产业资本|政策资本|GPU|CPO|ASIC|数据中心|算力|机器人|半导体|国债|政策性工具|基建|大模型|AI"):
        for match in re.findall(pattern, text, flags=re.I):
            token = re.sub(r"\s+", "", str(match))
            if token and token not in terms:
                terms.append(token)
            if len(terms) >= 4:
                return terms
    institution = _institution_from_text(text)
    if institution and institution not in terms:
        terms.insert(0, institution)
    return terms[:4] or ["核心观点"]


def _derive_flow_nodes(script: str, title: str) -> List[str]:
    text = f"{title} {script}"
    candidates = []
    dictionary = ["AI算力", "算力基础设施", "超长期国债", "政策性工具", "社会资本", "14x", "AI基建", "算力需求", "数据中心", "电力基础设施", "资本开支", "大模型", "产业链"]
    for item in dictionary:
        if item.lower() in text.lower() and item not in candidates:
            candidates.append(item)
    if len(candidates) >= 4:
        return candidates[:5]
    fallback = ["核心变量", "传导机制", "资金/需求放大", "产业落地"]
    return [*candidates, *[item for item in fallback if item not in candidates]][:5]


def _keyword_title(sentence: str, fallback: str) -> str:
    terms = _derive_highlight_terms(sentence)
    institution = _institution_from_text(sentence)
    for term in terms:
        if term and term not in {"核心观点", institution, "AI"}:
            return term
    return fallback


def _institution_from_text(text: str) -> str:
    patterns = [
        (r"摩根大通|J\.?P\.?\s*Morgan|JPMorgan", "摩根大通"),
        (r"野村|Nomura", "Nomura"),
        (r"高盛|Goldman", "高盛"),
        (r"瑞银|UBS", "瑞银"),
        (r"摩根士丹利|Morgan Stanley", "摩根士丹利"),
        (r"伯恩斯坦|Bernstein", "伯恩斯坦"),
        (r"中金|CICC", "中金"),
    ]
    for pattern, name in patterns:
        if re.search(pattern, str(text or ""), re.I):
            return name
    return ""


def _date_from_text(text: str) -> str:
    match = re.search(r"(20\d{2})[-_.]?(\d{2})[-_.]?(\d{2})", str(text or ""))
    if match:
        return f"{match.group(1)}-{match.group(2)}-{match.group(3)}"
    match = re.search(r"(?<!\d)(\d{6})(?!\d)", str(text or ""))
    if match:
        token = match.group(1)
        return f"20{token[:2]}-{token[2:4]}-{token[4:6]}"
    return ""


def _safe_text_list(value: Any, limit: int, item_limit: int) -> List[str]:
    raw = value if isinstance(value, list) else ([value] if value else [])
    result = []
    for item in raw:
        text = _trim(item, item_limit)
        if text and text not in result:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _pick(mapping: Dict[str, Any], *keys: str) -> Any:
    value: Any = mapping
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _clean_title(value: Any, limit: int) -> str:
    text = Path(str(value or "")).stem
    text = re.sub(r"\s+", " ", text).strip(" #")
    text = re.sub(r"[-_ ]?(20\d{6}|20\d{2}[-_.]?\d{2}[-_.]?\d{2}|\d{6})$", "", text).strip(" -_|｜")
    return _trim(text or "研报核心观点解读", limit)


def _trim(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    if len(text) <= limit:
        return text
    return text[: max(1, limit - 1)].rstrip("，,。；;：: ") + "…"
