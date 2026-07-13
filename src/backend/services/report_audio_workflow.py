import json
import logging
import os
import re
import subprocess
import wave
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


FEATURE_NAME = "report_pdf_audio_workflow"
QUEUE_NAME = "report_audio_workflow"
LOGGER = logging.getLogger(__name__)

DEFAULT_INITIAL_PROMPT = (
    "请解析上传的研报 PDF，提取核心观点、关键数据、风险提示和可用于口播的摘要。"
)
DEFAULT_REVIEW_PROMPT = (
    "担任首席责任主编，利用提供的PDF材料和可查网络信息，对上文内容逐条复核与勘误，"
    "确认事实准确、逻辑清晰、表达规范，列出每条修正点及依据，并给出最终优化后的信息版本，"
    "附上核对过程说明和参考来源。"
)
GEMINI_CAPTURE_ARTIFACT_MARKERS = (
    "自定义 Gem",
    "研报pdf解析视频文案说",
    "研报 pdf 解析视频文案说",
    "【研报气质】",
    "【Hook策略】",
    "【结尾情绪基调】",
    "Gemini 是一款 AI 工具",
)
GEMINI_CAPTURE_LABELS = (
    "研报气质",
    "Hook策略",
    "结尾情绪基调",
)


class ReportAudioWorkflowError(RuntimeError):
    pass


def storage_path_for_job(job_id: str, created_at: Optional[datetime] = None) -> str:
    created_at = created_at or datetime.utcnow()
    root = Path(_project_root()) / "data" / "processed" / "report_audio_workflow" / created_at.strftime("%Y%m%d")
    root.mkdir(parents=True, exist_ok=True)
    return str(root / f"{job_id}.json")


def audio_path_for_job(job_id: str, created_at: Optional[datetime] = None) -> str:
    created_at = created_at or datetime.utcnow()
    root = Path(_project_root()) / "data" / "processed" / "report_audio_workflow" / created_at.strftime("%Y%m%d")
    root.mkdir(parents=True, exist_ok=True)
    return str(root / f"{job_id}.wav")


def pdf_first_page_image_path_for_job(job_id: str, created_at: Optional[datetime] = None) -> str:
    created_at = created_at or datetime.utcnow()
    root = Path(_project_root()) / "data" / "processed" / "report_audio_workflow" / created_at.strftime("%Y%m%d")
    root.mkdir(parents=True, exist_ok=True)
    return str(root / f"{job_id}_pdf_first_page.jpg")


def render_pdf_first_page_image(pdf_path: str, output_path: str, *, dpi: int = 170) -> Dict[str, Any]:
    source = Path(str(pdf_path or ""))
    if not source.exists() or not source.is_file():
        raise ReportAudioWorkflowError(f"PDF 文件不存在，无法生成首屏截图: {pdf_path}")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    try:
        import fitz  # type: ignore

        with fitz.open(str(source)) as doc:
            if doc.page_count <= 0:
                raise ReportAudioWorkflowError("PDF 没有可渲染页面")
            page = doc.load_page(0)
            pix = page.get_pixmap(dpi=max(72, int(dpi)), colorspace=fitz.csRGB, alpha=False)
            pix.save(str(output))
            width = pix.width
            height = pix.height
    except ReportAudioWorkflowError:
        raise
    except Exception as exc:
        raise ReportAudioWorkflowError(f"PDF 首屏截图生成失败: {exc}") from exc

    return {
        "path": str(output),
        "bytes": output.stat().st_size,
        "width": width,
        "height": height,
        "page": 1,
        "dpi": dpi,
    }


def normalize_for_tts(
    raw_text: str,
    *,
    initial_text: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    normalizer_timeout: Optional[int] = None,
    allow_local_fallback: Optional[bool] = None,
) -> Dict[str, Any]:
    raw_text = (raw_text or "").strip()
    if not raw_text:
        raise ReportAudioWorkflowError("Gemini 结果为空，无法结构化")

    fallback_allowed = _local_fallback_allowed(allow_local_fallback)
    normalizer_error = ""
    try:
        payload = _deepseek_normalize(
            raw_text,
            initial_text=initial_text,
            metadata=metadata or {},
            timeout_seconds=normalizer_timeout,
        )
    except ReportAudioWorkflowError as exc:
        normalizer_error = str(exc)
        LOGGER.warning("DeepSeek normalizer failed: %s", exc)
        payload = None
    except (requests.RequestException, ValueError, KeyError, IndexError, TypeError) as exc:
        normalizer_error = str(exc)
        LOGGER.warning("DeepSeek normalizer returned unusable response: %s", exc)
        payload = None
    if not payload:
        if not fallback_allowed:
            detail = f"：{normalizer_error}" if normalizer_error else ""
            raise ReportAudioWorkflowError(
                f"结构化器不可用或返回不可用结果{detail}。已禁止 local_fallback 产出最终文案。"
            )
        LOGGER.warning("DeepSeek normalizer unavailable; local fallback explicitly enabled")
        payload = _fallback_normalize(raw_text)
    return _validate_structured_result(payload, raw_text)


def structured_result_quality_issue(structured: Dict[str, Any]) -> str:
    if not isinstance(structured, dict):
        return ""
    script = str(structured.get("final_script_for_tts") or "").strip()
    return _tts_script_quality_issue(script)


def clean_final_script_for_tts(value: Any) -> str:
    text = _clean_tts_script(value)
    if _looks_like_editor_review_notes(text):
        extracted = _clean_tts_script(_extract_final_script(text))
        if extracted and not _looks_like_editor_review_notes(extracted):
            text = extracted
    return _strip_trailing_section_stub(text)


def split_tts_text(text: str, max_chars: Optional[int] = None) -> List[str]:
    text = _clean_text(text)
    if not text:
        return []
    max_chars = max_chars or _int_env("REPORT_AUDIO_TTS_CHUNK_CHARS", 500)
    max_chars = max(200, min(max_chars, 5000))
    if len(text) <= max_chars:
        return [text]

    parts: List[str] = []
    current = ""
    for sentence in _split_sentences(text):
        if not sentence:
            continue
        if len(sentence) > max_chars:
            if current:
                parts.append(current.strip())
                current = ""
            parts.extend(_hard_chunks(sentence, max_chars))
            continue
        candidate = f"{current}{sentence}" if not current else f"{current}\n{sentence}"
        if len(candidate) > max_chars:
            parts.append(current.strip())
            current = sentence
        else:
            current = candidate
    if current.strip():
        parts.append(current.strip())
    return [part for part in parts if part]


def combine_wav_files(input_paths: List[str], output_path: str) -> Dict[str, Any]:
    paths = [Path(path) for path in input_paths if path and Path(path).exists()]
    if not paths:
        raise ReportAudioWorkflowError("没有可合并的 WAV 文件")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    params = None
    total_frames = 0
    with wave.open(str(output), "wb") as writer:
        for path in paths:
            with wave.open(str(path), "rb") as reader:
                if params is None:
                    params = reader.getparams()
                    writer.setparams(params)
                elif reader.getparams()[:3] != params[:3]:
                    raise ReportAudioWorkflowError(f"WAV 参数不一致，无法合并: {path}")
                frames = reader.readframes(reader.getnframes())
                total_frames += reader.getnframes()
                writer.writeframes(frames)

    sample_rate = params.framerate if params else 0
    seconds = total_frames / sample_rate if sample_rate else 0
    return {
        "wav_path": str(output),
        "bytes": output.stat().st_size,
        "sample_rate": sample_rate,
        "seconds": seconds,
        "chunks": len(paths),
    }


def synthesize_doubao_api_chunks(
    chunks: List[str],
    output_path: str,
    *,
    voice_type: str = "zh_female_mizai_uranus_bigtts",
) -> Dict[str, Any]:
    """使用豆包 WebSocket V3 API 合成分段音频，并统一转为工作流 WAV。"""
    from services.doubao_tts_ws import DoubaoTTSWebSocketClient

    texts = [str(item or "").strip() for item in chunks if str(item or "").strip()]
    if not texts:
        raise ReportAudioWorkflowError("豆包 API TTS 没有可合成的正文")

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    chunk_dir = output.parent / f"{output.stem}_doubao_api_chunks"
    chunk_dir.mkdir(parents=True, exist_ok=True)
    client = DoubaoTTSWebSocketClient()
    audio_paths = []
    for index, text in enumerate(texts, start=1):
        response = client.synthesize(
            text=text,
            speaker=voice_type,
            model="seed-tts-2.0-standard",
            audio_format="mp3",
            sample_rate=24000,
            bit_rate=128000,
            enable_subtitle=False,
            uid=f"report_audio_{output.stem}_{index}",
        )
        if not response.audio:
            raise ReportAudioWorkflowError(f"豆包 API TTS 第 {index} 段未返回音频")
        chunk_path = chunk_dir / f"{index:03d}.mp3"
        chunk_path.write_bytes(response.audio)
        audio_paths.append(chunk_path)

    concat_file = chunk_dir / "concat.txt"
    concat_file.write_text(
        "\n".join(f"file '{str(path.resolve()).replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'" for path in audio_paths),
        encoding="utf-8",
    )
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
                "-vn", "-acodec", "pcm_s16le", "-ar", "24000", "-ac", "1", str(output),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=600,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ReportAudioWorkflowError(f"豆包 API TTS 音频合并失败: {exc}") from exc

    with wave.open(str(output), "rb") as reader:
        sample_rate = reader.getframerate()
        seconds = reader.getnframes() / sample_rate if sample_rate else 0
    return {
        "wav_path": str(output),
        "bytes": output.stat().st_size,
        "sample_rate": sample_rate,
        "seconds": seconds,
        "chunks": len(audio_paths),
        "provider": "doubao_api",
        "voice_type": voice_type,
    }


def status_payload() -> Dict[str, Any]:
    deepseek_configured = bool(os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("REPORT_AUDIO_NORMALIZER_API_KEY"))
    fallback_allowed = _local_fallback_allowed()
    return {
        "configured": bool(deepseek_configured or fallback_allowed),
        "deepseek_configured": deepseek_configured,
        "normalizer_available": bool(deepseek_configured or fallback_allowed),
        "local_fallback_allowed": fallback_allowed,
        "normalizer_provider": "deepseek" if deepseek_configured else ("local_fallback" if fallback_allowed else "unavailable"),
        "normalizer_model": _normalizer_model() if deepseek_configured else None,
        "mode": "celery_orchestrator",
    }


def _deepseek_normalize(
    raw_text: str,
    *,
    initial_text: str,
    metadata: Dict[str, Any],
    timeout_seconds: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    api_key = os.environ.get("REPORT_AUDIO_NORMALIZER_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        return None

    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    model = _normalizer_model()
    timeout = _normalizer_timeout(timeout_seconds)
    system_prompt = (
        "你是内容流水线的结构化整理器。只做抽取和规整，不新增事实，不扩写观点。"
        "你必须输出严格 JSON，不要 Markdown，不要解释。"
    )
    user_prompt = "\n".join([
        "请把下面 Gemini 审核后的研报口播结果整理成严格 JSON。",
        "字段必须包含：",
        "- title: 字符串，最适合最终音频/内容列表展示的标题，优先从 Gemini 返回的标题方案、短标题、标准标题或核心观点中挑选并轻微润色",
        "- summary: 字符串，200字以内摘要",
        "- final_script_for_tts: 字符串，只包含要交给 TTS 朗读的最终口播正文，不包含修正清单、标题方案、参考来源说明、Markdown 标题",
        "- corrections: 数组，每项包含 issue、basis、action",
        "- titles: 对象，包含 standard 和 short 两个数组",
        "- landscape_title: 字符串，横屏视频及横屏封面的主标题，最多两行，用 \\n 换行；第一行优先使用“投行简称:核心对象+结论/动作”，第二行补充关键变量",
        "- landscape_subtitle: 字符串，横屏副标题，只能一行，控制在34个中文字符以内，补充数据、估值或行业范围",
        "- vertical_title: 字符串，竖屏视频及 B站封皮主标题，最多两行，用 \\n 换行；从 Gemini 内容中选择更适合移动端点击、且投资者更关心的矛盾或机会，不能照抄横屏标题",
        "- vertical_subtitle: 字符串，竖屏副标题，只能一行，控制在28个中文字符以内；补充一个最有信息差的数据、催化或结论，不能复述主标题",
        "- cover_title: 字符串，与 landscape_title 相同，作为旧版兼容字段",
        "- cover_subtitle: 字符串，与 landscape_subtitle 相同，作为旧版兼容字段",
        "- sources: 数组，引用来源说明",
        "- risk_notes: 数组，风险提示或合规提示",
        "- editor_notes: 字符串，简短说明整理策略",
        "如果原文中没有某字段内容，用空字符串或空数组。",
        "final_script_for_tts 必须适合直接朗读，保留必要的投资风险提示。",
        "final_script_for_tts 不能包含“第一部分、第二部分、开场与变量切换、产业背景、深度数据支撑、核心观测信号”等结构标签；这些是写作提纲，不是口播正文。",
        "",
        "标题选择规则：",
        "1. 先扫描 Gemini 审核后结果中的“标题方案、标准标题、短标题、封面标题、核心观点、段落标题”，从中选择最贴合研报结论的一条；只有没有可用标题时，才从正文抽取重写。",
        "2. title、landscape_title 和 vertical_title 必须忠于 Gemini 返回内容；涉及数字、订单、评级、目标价、市场规模、时间窗口时，必须在 Gemini 文本中明确出现。",
        "3. 禁止为了吸引点击新增或夸大事实，尤其不要凭空使用“引爆、颠覆、巨头转身、百亿/千亿/万亿、大单、暴涨、暴跌”等词。",
        "4. landscape_title 和 vertical_title 每行控制在 18 个中文字符以内；如果一行已经足够好，就不要硬凑第二行。",
        "5. 两套副标题都只保留一行事实补充，不要复述主标题，不要写成完整长句。",
        "6. 横屏和竖屏必须从 Gemini 返回中选择不同的信息角度；横屏偏完整、稳健，竖屏偏核心矛盾、信息差和点击吸引力，但都不得新增事实。",
        "",
        f"metadata={json.dumps(metadata, ensure_ascii=False)}",
        "",
        "【第一轮结果，可用于上下文】",
        _compact_text(initial_text, _int_env("REPORT_AUDIO_INITIAL_CONTEXT_CHARS", 4000)),
        "",
        "【Gemini 审核后结果】",
        _compact_text(raw_text, _int_env("REPORT_AUDIO_NORMALIZER_INPUT_CHARS", 18000)),
    ])

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
            "temperature": 0.1,
        },
        timeout=timeout,
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        body = response.text[:800] if response is not None else ""
        raise ReportAudioWorkflowError(f"DeepSeek 结构化失败: HTTP {response.status_code} {body}") from exc
    try:
        data = response.json()
    except ValueError as exc:
        body = response.text[:800] if response is not None else ""
        raise ReportAudioWorkflowError(f"DeepSeek 结构化失败: 返回非 JSON 响应 {body}") from exc

    content = (((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    if not content:
        LOGGER.warning("DeepSeek normalizer returned empty content; using local fallback")
        return None

    try:
        payload = _json_from_text(content)
    except (json.JSONDecodeError, TypeError) as exc:
        LOGGER.warning(
            "DeepSeek normalizer returned non-JSON content; using local fallback. preview=%r",
            _compact_text(content, 500),
        )
        return None
    if isinstance(payload, dict):
        payload["provider"] = "deepseek"
    return payload


def _normalizer_timeout(timeout_seconds: Optional[int] = None) -> int:
    if timeout_seconds is not None:
        try:
            return max(1, int(timeout_seconds))
        except (TypeError, ValueError):
            pass
    return _int_env("REPORT_AUDIO_NORMALIZER_TIMEOUT", 120)


def _validate_structured_result(payload: Dict[str, Any], raw_text: str) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {}
    script = clean_final_script_for_tts(payload.get("final_script_for_tts") or "")
    if not script:
        script = clean_final_script_for_tts(_extract_final_script(raw_text))
    if not script:
        raise ReportAudioWorkflowError("结构化结果缺少 final_script_for_tts")
    quality_issue = _tts_script_quality_issue(script)
    if quality_issue:
        raise ReportAudioWorkflowError(f"结构化结果不可用于朗读: {quality_issue}")

    landscape_title = _clean_multiline_text(payload.get("landscape_title") or payload.get("cover_title") or "")[:160]
    landscape_subtitle = _clean_text(payload.get("landscape_subtitle") or payload.get("cover_subtitle") or "")[:120]
    vertical_title = _clean_multiline_text(payload.get("vertical_title") or "")[:160]
    vertical_subtitle = _clean_text(payload.get("vertical_subtitle") or "")[:120]
    result = {
        "title": _clean_text(payload.get("title") or "")[:300],
        "summary": _clean_text(payload.get("summary") or "")[:1000],
        "landscape_title": landscape_title,
        "landscape_subtitle": landscape_subtitle,
        "vertical_title": vertical_title or landscape_title,
        "vertical_subtitle": vertical_subtitle or landscape_subtitle,
        "cover_title": landscape_title,
        "cover_subtitle": landscape_subtitle,
        "final_script_for_tts": script,
        "corrections": _safe_list(payload.get("corrections"), 50),
        "titles": payload.get("titles") if isinstance(payload.get("titles"), dict) else {"standard": [], "short": []},
        "sources": _safe_list(payload.get("sources"), 30),
        "risk_notes": _safe_list(payload.get("risk_notes"), 20),
        "editor_notes": _clean_text(payload.get("editor_notes") or "")[:2000],
        "provider": payload.get("provider") or "local_fallback",
        "raw_text_chars": len(raw_text),
    }
    return result


def _fallback_normalize(raw_text: str) -> Dict[str, Any]:
    title_pack = _extract_fallback_title_pack(raw_text)
    script = clean_final_script_for_tts(_extract_final_script(raw_text) or raw_text)
    landscape_title = title_pack.get("cover_title") or ""
    landscape_subtitle = title_pack.get("cover_subtitle") or ""
    return {
        "title": title_pack.get("title") or "",
        "summary": "",
        "landscape_title": landscape_title,
        "landscape_subtitle": landscape_subtitle,
        "vertical_title": landscape_title,
        "vertical_subtitle": landscape_subtitle,
        "cover_title": landscape_title,
        "cover_subtitle": landscape_subtitle,
        "final_script_for_tts": script,
        "corrections": [],
        "titles": title_pack.get("titles") or {"standard": [], "short": []},
        "sources": [],
        "risk_notes": [],
        "editor_notes": "DeepSeek 未配置或返回不可用结果，使用本地规则抽取最终口播正文。",
        "provider": "local_fallback",
    }


def _extract_final_script(raw_text: str) -> str:
    text = _clean_text(raw_text)
    markers = [
        "最终优化版播客文案",
        "最终优化后的播客文案",
        "最终版播客文案",
        "优化后的最终播客文案",
        "最终优化版口播文案",
        "最终优化后的口播文案",
        "最终版口播文案",
        "最终口播正文",
        "最终口播文案",
        "最终优化后的信息版本",
        "最终文案",
        "口播文案",
    ]
    start = -1
    for marker in markers:
        index = text.find(marker)
        if index >= 0:
            start = index + len(marker)
            break
    if start >= 0:
        text = text[start:].strip(" ：:。")

    end_markers = ["标题与包装方案", "标准版标题方案", "标题方案", "16字以内", "核对过程说明"]
    end_positions = [text.find(marker) for marker in end_markers if text.find(marker) > 200]
    if end_positions:
        text = text[:min(end_positions)].strip()
    return _strip_trailing_section_stub(_strip_leading_style_fragments(_strip_gemini_capture_artifacts(text)))


def _extract_fallback_title_pack(raw_text: str) -> Dict[str, Any]:
    text = _clean_text(raw_text)
    standard_titles = _extract_labeled_values(
        text,
        r"主标题[:：]\s*(.+?)(?=\s+副标题[:：]|\s+节目介绍[:：]|\s+方案[A-C一二三]|\s+版本[一二三\d]|$)",
        limit=5,
    )
    short_titles = _extract_labeled_values(
        text,
        r"版本[一二三\d]+\s+主标题[:：]\s*(.+?)(?=\s+副标题[:：]|\s+版本[一二三\d]|$)",
        limit=5,
    )
    subtitles = _extract_labeled_values(
        text,
        r"副标题[:：]\s*(.+?)(?=\s+节目介绍[:：]|\s+方案[A-C一二三]|\s+版本[一二三\d]|$)",
        limit=3,
    )
    title = (standard_titles or short_titles or [""])[0]
    return {
        "title": title,
        "cover_title": title,
        "cover_subtitle": subtitles[0] if subtitles else "",
        "titles": {
            "standard": standard_titles,
            "short": short_titles,
        },
    }


def _extract_labeled_values(text: str, pattern: str, *, limit: int) -> List[str]:
    values: List[str] = []
    for match in re.finditer(pattern, text, flags=re.S):
        value = _clean_text(match.group(1))
        if value and value not in values:
            values.append(value[:300])
        if len(values) >= limit:
            break
    return values


def _json_from_text(value: str) -> Dict[str, Any]:
    text = (value or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    match = re.search(r"\{.*\}", text, flags=re.S)
    if match:
        text = match.group(0)
    return json.loads(text)


def _split_sentences(text: str) -> List[str]:
    pieces = re.split(r"(?<=[。！？!?；;])\s*", text)
    return [piece.strip() for piece in pieces if piece.strip()]


def _hard_chunks(text: str, max_chars: int) -> List[str]:
    return [text[index:index + max_chars].strip() for index in range(0, len(text), max_chars)]


def _safe_list(value: Any, limit: int) -> List[Any]:
    if not isinstance(value, list):
        return []
    return value[:limit]


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _clean_tts_script(value: Any) -> str:
    text = _strip_gemini_capture_artifacts(value)
    text = re.sub(r"(^|\s)PDF(?:\s*\+\s*\d+)?(?=\s|$)", " ", text, flags=re.I)
    text = _strip_leading_style_fragments(text)
    text = _strip_tts_structure_labels(text)
    return _strip_trailing_section_stub(_clean_text(text))


def _strip_leading_style_fragments(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    text = re.sub(
        r"^(?:[：:、，。；;\s]*(?:[\u4e00-\u9fffA-Za-z0-9]{1,14}型|聚焦式|散点式|悬念式|故事式)){1,8}",
        " ",
        text,
    )
    return _clean_text(text)


def _strip_trailing_section_stub(value: Any) -> str:
    text = _clean_text(value)
    return re.sub(r"\s*(?:[一二三四五六七八九十]|\d+)[、.．]\s*$", "", text).strip()


def _strip_tts_structure_labels(value: Any) -> str:
    text = str(value or "").replace("\r", "\n")
    if not text.strip():
        return ""

    # Remove outline headings that DeepSeek sometimes leaves inside the text
    # handed to Doubao, for example: "第一部分：开场与变量切换（引入悬念）".
    boundary = r"(^|[\n。！？!?；;]\s*)"
    section_index = r"(?:第\s*)?(?:[一二三四五六七八九十\d]+)\s*部分"
    text = re.sub(
        boundary + section_index + r"\s*[:：]\s*[^。！？!?；;\n]{0,80}?（[^）\n]{1,80}）\s*",
        lambda match: match.group(1),
        text,
    )
    text = re.sub(
        boundary + section_index + r"\s*[:：]\s*[^。！？!?；;\n]{1,28}(?=\s+(?:过去|今天|接下来|关于|最后|首先|其次|再来看|从|在|我们|这|高盛|摩根|大摩|美银|瑞银|花旗|中金|华泰|欢迎))",
        lambda match: match.group(1),
        text,
    )
    return _clean_text(text)


def _looks_like_editor_review_notes(value: Any) -> bool:
    text = _clean_text(value)
    return bool(re.search(
        r"作为(?:首席)?内容主编|播客文案初稿|核对过程与勘误说明|原稿表述|原文依据|修正说明|勘误点",
        text,
    ))


def _strip_gemini_capture_artifacts(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return ""
    text = re.sub(
        r"^\s*(?:研报\s*pdf\s*解析视频文案\s*)?(?:自定义\s*Gem\s*)?(?:研报\s*pdf\s*解析视频文案说\s*)?",
        "",
        text,
        flags=re.I,
    ).strip()
    for label in GEMINI_CAPTURE_LABELS:
        text = re.sub(rf"【{re.escape(label)}】\s*[\w\u4e00-\u9fff-]{{1,16}}", " ", text)
        text = re.sub(rf"【{re.escape(label)}】", " ", text)
    text = re.sub(r"Gemini\s+是一款\s+AI\s+工具.*$", " ", text, flags=re.I)
    text = re.sub(r"(?<![A-Za-z0-9])PDF\s*\+\s*\d+(?![A-Za-z0-9])", " ", text, flags=re.I)
    return _clean_text(text)


def _tts_script_quality_issue(value: Any) -> str:
    text = _clean_text(value)
    if not text:
        return "正文为空"
    compact = re.sub(r"\s+", "", text).lower()
    if re.search(r"研报\s*pdf\s*解析视频文案\s*自定义\s*gem", text, flags=re.I):
        return "包含 Gemini 页面标题残留"
    if re.search(r"研报\s*pdf\s*解析视频文案说", text, flags=re.I):
        return "包含 Gemini 回复区页面残留"
    for marker in GEMINI_CAPTURE_ARTIFACT_MARKERS:
        if re.sub(r"\s+", "", marker).lower() in compact:
            return f"包含页面/提示词残留: {marker}"
    if re.search(r"(?<![A-Za-z0-9])PDF\s*\+\s*\d+(?![A-Za-z0-9])", text, flags=re.I):
        return "包含 PDF 上传控件残留"
    if _looks_like_editor_review_notes(text):
        return "包含编辑复核/勘误说明残留"
    if _contains_tts_structure_label(text):
        return "包含分段结构标签"
    return ""


def _contains_tts_structure_label(value: Any) -> bool:
    text = _clean_text(value)
    if not text:
        return False
    return bool(re.search(
        r"(^|[。！？!?；;]\s*)(?:第\s*)?[一二三四五六七八九十\d]+\s*部分\s*[:：]",
        text,
    ))


def _clean_multiline_text(value: Any) -> str:
    lines = [_clean_text(line) for line in str(value or "").replace("\r", "\n").split("\n")]
    return "\n".join(line for line in lines if line)


def _compact_text(value: str, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    head = max_chars // 2
    tail = max_chars - head
    return f"{text[:head]}\n\n...[中间省略 {len(text) - max_chars} 字]...\n\n{text[-tail:]}"


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def _local_fallback_allowed(value: Optional[bool] = None) -> bool:
    if value is not None:
        return bool(value)
    raw = str(os.environ.get("REPORT_AUDIO_ALLOW_LOCAL_FALLBACK", "")).strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _normalizer_model() -> str:
    return os.environ.get("REPORT_AUDIO_NORMALIZER_MODEL") or os.environ.get("DEEPSEEK_MODEL") or "deepseek-v4-pro"


def _project_root() -> str:
    return str(Path(__file__).resolve().parents[3])
