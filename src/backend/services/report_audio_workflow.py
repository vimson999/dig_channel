import copy
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
DEFAULT_SCRIPT_REGENERATION_MIN_CHARS = 1500
DEFAULT_INITIAL_SCRIPT_MIN_CHARS = DEFAULT_SCRIPT_REGENERATION_MIN_CHARS
PDF_EVIDENCE_REVIEW_CONTRACT = (
    "最后另起一段输出以 PDF_EVIDENCE_V1_JSON: 开头的单行严格 JSON，"
    "格式为 {\"schema_version\":\"report_pdf_evidence_v1\",\"claims\":[...]}; 每条 claims 必须包含"
    "claim_text、script_quote、pdf_page、pdf_quote、claim_type、evidence_kind、confidence、"
    "use_as_visual，可选 crop={x,y,width,height}（均为0到1）。只填写能从 PDF 明确定位的内容，"
    "不要使用 Markdown 代码围栏；无法定位的主张不要伪造证据。"
)
DEFAULT_REVIEW_PROMPT_BASE = (
    "担任首席责任主编，利用提供的PDF材料和可查网络信息，对上文内容逐条复核与勘误，"
    "确认事实准确、逻辑清晰、表达规范，列出每条修正点及依据，并给出最终优化后的信息版本，"
    "附上核对过程说明和参考来源。"
)
DEFAULT_REVIEW_PROMPT = f"{DEFAULT_REVIEW_PROMPT_BASE}{PDF_EVIDENCE_REVIEW_CONTRACT}"
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
TITLE_REVIEW_SCHEMA_VERSION = "report_audio_title_review_20260724_v1"


class ReportAudioWorkflowError(RuntimeError):
    pass


def ensure_pdf_evidence_review_contract(value: Any) -> str:
    """Append the server-owned evidence contract to legacy/custom review prompts.

    The exact contract is idempotent and cannot be removed by a stale frontend,
    cached form value, or a task-level prompt override.
    """

    prompt = str(value or "").strip()
    if not prompt:
        return DEFAULT_REVIEW_PROMPT
    if PDF_EVIDENCE_REVIEW_CONTRACT in prompt:
        return prompt
    prompt = _strip_pdf_evidence_review_contract(prompt)
    return f"{prompt}\n\n{PDF_EVIDENCE_REVIEW_CONTRACT}"


def review_prompt_for_pdf_evidence(value: Any, *, enabled: bool) -> str:
    """Return a review prompt with the server-owned evidence contract toggled.

    New tasks can disable evidence generation without relying on a possibly
    stale browser form.  Current and cached legacy variants of the server-owned
    suffix are removed while unrelated custom review instructions remain.
    """

    prompt = str(value or "").strip()
    if not prompt:
        prompt = DEFAULT_REVIEW_PROMPT_BASE
    prompt = _strip_pdf_evidence_review_contract(prompt)
    if enabled:
        return ensure_pdf_evidence_review_contract(prompt)
    return prompt


def _strip_pdf_evidence_review_contract(value: Any) -> str:
    """Remove current and cached legacy variants of our evidence suffix.

    Older frontend bundles inserted one optional space after ``必须包含``.
    Exact string replacement therefore left the entire evidence contract in
    new evidence-disabled jobs.  Match the server-owned opening and closing
    sentences instead, while preserving unrelated custom review instructions.
    """

    prompt = str(value or "").strip()
    if not prompt:
        return ""
    prompt = prompt.replace(PDF_EVIDENCE_REVIEW_CONTRACT, "").strip()
    if "PDF_EVIDENCE_V1_JSON" not in prompt and "report_pdf_evidence_v1" not in prompt:
        return prompt

    pattern = re.compile(
        r"(?:\s*\n*)?最后另起一段输出以\s*PDF_EVIDENCE_V1_JSON:\s*开头的单行严格\s*JSON，"
        r".*?无法定位的主张不要伪造证据。",
        re.DOTALL,
    )
    cleaned = pattern.sub("", prompt).strip()
    if "PDF_EVIDENCE_V1_JSON" not in cleaned and "report_pdf_evidence_v1" not in cleaned:
        return cleaned

    # Last-resort compatibility for punctuation/whitespace-only legacy edits.
    marker_positions = [
        position
        for marker in ("PDF_EVIDENCE_V1_JSON", "report_pdf_evidence_v1")
        if (position := cleaned.find(marker)) >= 0
    ]
    if not marker_positions:
        return cleaned
    marker_position = min(marker_positions)
    start = cleaned.rfind("最后另起一段", 0, marker_position + 1)
    closing = "无法定位的主张不要伪造证据。"
    end = cleaned.find(closing, marker_position)
    if start >= 0 and end >= 0:
        cleaned = f"{cleaned[:start]}{cleaned[end + len(closing):]}".strip()
    return cleaned


def script_regeneration_prompts(
    initial_prompt: Any,
    review_prompt: Any,
    *,
    minimum_chars: int = DEFAULT_SCRIPT_REGENERATION_MIN_CHARS,
) -> Dict[str, str]:
    """Build a fresh-script request without inheriting the evidence contract."""

    try:
        minimum = max(DEFAULT_SCRIPT_REGENERATION_MIN_CHARS, min(20000, int(minimum_chars)))
    except (TypeError, ValueError):
        minimum = DEFAULT_SCRIPT_REGENERATION_MIN_CHARS
    initial = str(initial_prompt or DEFAULT_INITIAL_PROMPT).strip()
    review = review_prompt_for_pdf_evidence(review_prompt, enabled=False)
    rewrite_directive = (
        "这是一次全新重写，请重新独立理解 PDF，不要复用或缩写上一版文案。"
        f"最终可直接口播的中文正文必须不少于 {minimum} 字。"
    )
    final_directive = (
        f"请只交付一份完整、连贯、可直接朗读的最终中文口播文案；正文不少于 {minimum} 字，"
        "字数不包含标题、过程说明、引用清单或 Markdown 标记。不得用‘略’、‘同上’或提纲代替正文。"
    )
    return {
        "prompt": f"{initial}\n\n{rewrite_directive}",
        "review_prompt": f"{review}\n\n{final_directive}",
    }


def initial_submission_prompts(
    initial_prompt: Any,
    review_prompt: Any,
    *,
    minimum_chars: int = DEFAULT_INITIAL_SCRIPT_MIN_CHARS,
) -> Dict[str, str]:
    """Attach the server-owned spoken-script length contract to a new job."""

    try:
        minimum = max(DEFAULT_INITIAL_SCRIPT_MIN_CHARS, min(20000, int(minimum_chars)))
    except (TypeError, ValueError):
        minimum = DEFAULT_INITIAL_SCRIPT_MIN_CHARS
    initial = str(initial_prompt or DEFAULT_INITIAL_PROMPT).strip()
    review = str(review_prompt or DEFAULT_REVIEW_PROMPT).strip()
    initial_directive = (
        "首轮解析就要形成完整口播素材，充分保留 PDF 中与结论有关的背景、关键数据、"
        f"逻辑链条、分歧和风险；最终可直接朗读的中文正文必须不少于 {minimum} 字。"
    )
    final_directive = (
        f"最终优化稿中的可直接朗读中文正文必须不少于 {minimum} 字；"
        "字数不包含标题、核对过程、修正清单、参考来源、证据 JSON 或 Markdown 标记。"
        "不得用提纲、重复句或无事实依据的扩写凑字。"
    )
    return {
        "prompt": f"{initial}\n\n{initial_directive}",
        "review_prompt": f"{review}\n\n{final_directive}",
    }


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
    minimum_script_chars: int = 0,
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
            minimum_script_chars=minimum_script_chars,
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
    return _validate_structured_result(
        payload,
        raw_text,
        minimum_script_chars=minimum_script_chars,
    )


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


def assert_final_tts_text_clean(value: Any) -> str:
    """Fail closed when non-spoken artifacts survive deterministic cleaning."""

    text = str(value or "").strip()
    if not text:
        raise ReportAudioWorkflowError("TTS 正文为空")
    checks = (
        (r"```|~~~", "包含 Markdown 代码围栏"),
        (r"https?://|www\.", "包含 URL"),
        (r"\[[^\]\n]{1,120}\]\s*\([^)]+\)", "包含 Markdown 链接"),
        (
            r"(?i)(?<![A-Za-z0-9_])(?:cite|citation|source|reference)\s*(?:[:：#]?\s*\d+|\[|\()",
            "包含引用标记",
        ),
        (r"(?:参考|资料|数据|信息)?来源\s*[:：]", "包含来源说明"),
        (r"[\[【]\s*\d{1,4}\s*[\]】]", "包含数字脚注"),
        (
            r"(?i)(?:PDF_EVIDENCE|evidence_map|evidence_mapping|pdf_quote|script_quote|schema_version)"
            r"\s*[\"']?\s*[:：]",
            "包含证据结构字段",
        ),
        (r"^\s*[\{\[]\s*\"[A-Za-z_\u4e00-\u9fff][^\"\\]{0,80}\"\s*:", "包含结构 JSON"),
    )
    for pattern, reason in checks:
        if re.search(pattern, text):
            raise ReportAudioWorkflowError(f"TTS 正文终检失败: {reason}")
    return text


def split_tts_text(text: str, max_chars: Optional[int] = None) -> List[str]:
    text = _clean_text(text)
    if not text:
        return []
    assert_final_tts_text_clean(text)
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
        "title_review_model": _title_review_model() if deepseek_configured else None,
        "mode": "celery_orchestrator",
    }


def review_report_cover_titles(
    structured: Dict[str, Any],
    *,
    source_text: str = "",
    pdf_filename: str = "",
    pdf_topic_title: str = "",
    timeout_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    """Generate and independently review cover titles without blocking the workflow.

    The original PDF title is a formal baseline. A generated title only replaces it
    when the reviewer scores it materially higher for the target format.
    """
    result = dict(structured or {})
    if (result.get("title_review") or {}).get("schema_version") == TITLE_REVIEW_SCHEMA_VERSION:
        return _normalize_existing_title_review(result)

    original_title = _pdf_title_baseline(pdf_filename, pdf_topic_title)
    candidates = _base_title_candidates(result, original_title)
    generation_error = ""
    review_error = ""
    generation_model = _title_generation_model()
    review_model = _title_review_model()

    try:
        generated = _deepseek_generate_title_candidates(
            result,
            source_text=source_text,
            original_title=original_title,
            timeout_seconds=timeout_seconds,
        )
        candidates = _dedupe_title_candidates(candidates + generated)
    except Exception as exc:  # The title layer must never stop audio production.
        generation_error = str(exc)
        LOGGER.warning("Report title candidate generation failed: %s", exc)

    review_candidates = _title_review_candidate_subset(candidates)
    review_payload: Dict[str, Any] = {}
    try:
        review_payload = _deepseek_review_title_candidates(
            result,
            candidates=review_candidates,
            source_text=source_text,
            original_title=original_title,
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:  # Keep the existing structured titles on review failure.
        review_error = str(exc)
        LOGGER.warning("Report title review failed: %s", exc)

    evaluated = _merge_title_evaluations(review_candidates, review_payload.get("evaluations"))
    selected = {}
    if review_payload:
        selected = _select_reviewed_titles(evaluated, review_payload)
        landscape = selected.get("landscape") or {}
        vertical = selected.get("vertical") or {}
        if landscape.get("title"):
            result["landscape_title"] = landscape["title"]
            result["landscape_subtitle"] = landscape.get("subtitle") or ""
            result["cover_title"] = landscape["title"]
            result["cover_subtitle"] = landscape.get("subtitle") or ""
        if vertical.get("title"):
            result["vertical_title"] = vertical["title"]
            result["vertical_subtitle"] = vertical.get("subtitle") or ""

    status = "completed"
    if review_error or not review_payload:
        status = "fallback"
    elif generation_error:
        status = "completed_with_generation_fallback"
    result["title_review"] = {
        "schema_version": TITLE_REVIEW_SCHEMA_VERSION,
        "status": status,
        "generation_model": generation_model,
        "review_model": review_model,
        "original_pdf_title": original_title,
        "selected": selected,
        "candidates": evaluated,
        "decision_summary": _clean_text(review_payload.get("decision_summary") or "")[:1000],
        "generation_error": generation_error[:1000],
        "review_error": review_error[:1000],
    }
    return result


def _deepseek_generate_title_candidates(
    structured: Dict[str, Any],
    *,
    source_text: str,
    original_title: str,
    timeout_seconds: Optional[int] = None,
) -> List[Dict[str, Any]]:
    prompt = "\n".join([
        "你是财经视频封面标题编辑。只依据所给研报内容生成候选，不得新增事实。",
        "请输出严格 JSON：{\"candidates\":[{\"angle\":\"stable|opportunity|data|contradiction|original_refined\","
        "\"title\":\"最多两行，用\\\\n换行\",\"subtitle\":\"一行补充信息\",\"evidence\":\"原文依据\"}]}。",
        "总共生成 10 条：稳健研报型3条、机会发现型3条、数据驱动型2条、矛盾型2条。",
        "规则：主标题每行不超过18个中文字符，最多两行；副标题不超过28个中文字符；"
        "不要使用竖线分隔，不堆砌三个以上概念，不把矛盾和负面词当固定公式。",
        "必须准确表达研报主结论。原始 PDF 标题是强基准，至少给出一条忠实压缩版。",
        f"原始PDF标题：{original_title}",
        f"现有结构化标题：{json.dumps(_title_context(structured), ensure_ascii=False)}",
        "研报内容：",
        _compact_text(source_text or structured.get("final_script_for_tts") or "", 12000),
    ])
    payload = _deepseek_json_call(
        prompt,
        model=_title_generation_model(),
        timeout_seconds=timeout_seconds,
        temperature=0.35,
        purpose="标题候选生成",
    )
    raw_candidates = payload.get("candidates") if isinstance(payload, dict) else []
    candidates = []
    for index, item in enumerate(raw_candidates or [], start=1):
        if not isinstance(item, dict):
            continue
        candidate = _clean_title_candidate(
            item,
            candidate_id=f"generated_{index}",
            source="generated",
        )
        if candidate:
            candidates.append(candidate)
    if not candidates:
        raise ReportAudioWorkflowError("DeepSeek 标题候选生成没有返回有效候选")
    return candidates


def _deepseek_review_title_candidates(
    structured: Dict[str, Any],
    *,
    candidates: List[Dict[str, Any]],
    source_text: str,
    original_title: str,
    timeout_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    prompt = "\n".join([
        "你是独立的财经内容责任主编。评审候选标题，不负责继续创作。",
        "输出严格 JSON，包含 evaluations、landscape_candidate_id、vertical_candidate_id、"
        "backup_candidate_id、decision_summary。",
        "evaluations 每项必须包含 candidate_id、thesis_alignment、factuality、information_gain、"
        "cover_readability、restraint、landscape_score、vertical_score（均0-100）、reason、rejection_reason。",
        "reason 和 rejection_reason 各不超过50个中文字符，不要复述候选标题或研报正文。",
        "评分重点依次为：是否贴合原研报主结论、事实是否可靠、信息增量、两行封面可读性、表达是否克制。",
        "横屏偏完整稳健；竖屏/B站可更敏锐，但不得用夸张矛盾替代主结论。",
        "原始 PDF 标题是正式基准：AI 候选没有明显胜出时必须保留原始标题。",
        "硬性口径：原始 PDF 标题的语义核心就是研报主论点。不得因为正文里出现“核心矛盾”等写作措辞，"
        "擅自把目标价微调、现金流压力或戏剧性反差改判成研报主论点。",
        "original_refined 候选是原题的封面压缩版；渲染器会自动补投行名称，不能因它未写机构名而扣分。",
        "若候选没有保留原题的核心方向，只是事实正确或更有冲突感，thesis_alignment 不得超过70，"
        "也不得选为横屏第一。竖屏可以换角度，但仍须保留原题核心方向。",
        "含竖线分隔、超过两行、堆砌三个以上概念或把“却、反而、承压”当固定套路的候选，"
        "cover_readability 或 restraint 必须明显扣分。",
        f"原始PDF标题：{original_title}",
        f"候选：{json.dumps(candidates, ensure_ascii=False)}",
        f"现有结构化信息：{json.dumps(_title_context(structured), ensure_ascii=False)}",
        "研报内容：",
        _compact_text(source_text or structured.get("final_script_for_tts") or "", 12000),
    ])
    payload = _deepseek_json_call(
        prompt,
        model=_title_review_model(),
        timeout_seconds=timeout_seconds,
        temperature=0.05,
        purpose="标题独立评审",
    )
    evaluations = payload.get("evaluations") if isinstance(payload, dict) else None
    if not isinstance(evaluations, list) or not any(
        isinstance(item, dict) and item.get("candidate_id") for item in evaluations
    ):
        raise ReportAudioWorkflowError("DeepSeek 标题独立评审没有返回有效评分")
    return payload


def _deepseek_json_call(
    prompt: str,
    *,
    model: str,
    timeout_seconds: Optional[int],
    temperature: float,
    purpose: str,
) -> Dict[str, Any]:
    api_key = os.environ.get("REPORT_AUDIO_NORMALIZER_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        raise ReportAudioWorkflowError(f"DeepSeek 未配置，无法执行{purpose}")
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    response = requests.post(
        f"{base_url.rstrip('/')}/v1/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={
            "model": model,
            "messages": [
                {"role": "system", "content": "只输出严格 JSON，不要 Markdown，不要解释。"},
                {"role": "user", "content": prompt},
            ],
            "temperature": temperature,
            "max_tokens": _int_env("REPORT_AUDIO_TITLE_REVIEW_MAX_TOKENS", 8192),
        },
        timeout=_title_review_timeout(timeout_seconds),
    )
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise ReportAudioWorkflowError(
            f"DeepSeek {purpose}失败: HTTP {response.status_code} {response.text[:800]}"
        ) from exc
    try:
        response_payload = response.json()
        choice = (response_payload.get("choices") or [{}])[0]
        content = ((choice.get("message") or {}).get("content") or "")
        payload = _json_from_text(content)
    except (ValueError, json.JSONDecodeError, TypeError, KeyError, IndexError) as exc:
        finish_reason = ""
        try:
            finish_reason = str(choice.get("finish_reason") or "")
        except (NameError, AttributeError):
            pass
        raise ReportAudioWorkflowError(
            f"DeepSeek {purpose}返回不可用 JSON"
            f"{f'（finish_reason={finish_reason}）' if finish_reason else ''}"
        ) from exc
    if not isinstance(payload, dict):
        raise ReportAudioWorkflowError(f"DeepSeek {purpose}返回结构错误")
    return payload


def _base_title_candidates(structured: Dict[str, Any], original_title: str) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    if original_title:
        candidates.append(_clean_title_candidate(
            {"title": original_title, "subtitle": "", "angle": "original"},
            candidate_id="original_pdf",
            source="original_pdf",
        ))
    pairs = [
        ("existing_landscape", structured.get("landscape_title") or structured.get("cover_title"),
         structured.get("landscape_subtitle") or structured.get("cover_subtitle"), "existing"),
        ("existing_vertical", structured.get("vertical_title"), structured.get("vertical_subtitle"), "existing"),
        ("existing_general", structured.get("title"), "", "existing"),
    ]
    for candidate_id, title, subtitle, source in pairs:
        candidate = _clean_title_candidate(
            {"title": title, "subtitle": subtitle, "angle": source},
            candidate_id=candidate_id,
            source=source,
        )
        if candidate:
            candidates.append(candidate)
    title_groups = structured.get("titles") if isinstance(structured.get("titles"), dict) else {}
    for group_name in ("standard", "short"):
        for index, item in enumerate(title_groups.get(group_name) or [], start=1):
            title = item.get("title") if isinstance(item, dict) else item
            subtitle = item.get("subtitle") if isinstance(item, dict) else ""
            candidate = _clean_title_candidate(
                {"title": title, "subtitle": subtitle, "angle": group_name},
                candidate_id=f"normalized_{group_name}_{index}",
                source="normalized",
            )
            if candidate:
                candidates.append(candidate)
    return _dedupe_title_candidates(candidates)


def _clean_title_candidate(
    item: Dict[str, Any],
    *,
    candidate_id: str,
    source: str,
) -> Optional[Dict[str, Any]]:
    title = _clean_multiline_text(item.get("title") or "")[:180]
    if not title:
        return None
    return {
        "candidate_id": candidate_id,
        "source": source,
        "angle": _clean_text(item.get("angle") or "")[:40],
        "title": title,
        "subtitle": _clean_text(item.get("subtitle") or "")[:100],
        "evidence": _clean_text(item.get("evidence") or "")[:500],
    }


def _dedupe_title_candidates(candidates: List[Optional[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    result = []
    seen = set()
    for candidate in candidates:
        if not candidate:
            continue
        key = re.sub(r"\s+", "", f"{candidate.get('title', '')}{candidate.get('subtitle', '')}").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        result.append(candidate)
    return result


def _title_review_candidate_subset(candidates: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep a diverse, bounded review set so Pro cannot exhaust its JSON budget."""
    result: List[Dict[str, Any]] = []
    seen = set()

    def add(candidate: Optional[Dict[str, Any]]) -> None:
        if not candidate:
            return
        candidate_id = candidate.get("candidate_id")
        if not candidate_id or candidate_id in seen:
            return
        seen.add(candidate_id)
        result.append(candidate)

    by_id = {item.get("candidate_id"): item for item in candidates}
    for candidate_id in ("original_pdf", "existing_landscape", "existing_vertical"):
        add(by_id.get(candidate_id))

    quotas = {
        "original_refined": 1,
        "stable": 2,
        "opportunity": 2,
        "data": 2,
        "contradiction": 1,
    }
    for angle, limit in quotas.items():
        count = 0
        for candidate in candidates:
            if candidate.get("source") != "generated" or candidate.get("angle") != angle:
                continue
            add(candidate)
            count += 1
            if count >= limit:
                break

    for candidate in candidates:
        if len(result) >= 11:
            break
        add(candidate)
    return result


def _merge_title_evaluations(
    candidates: List[Dict[str, Any]],
    evaluations: Any,
) -> List[Dict[str, Any]]:
    evaluation_map = {
        str(item.get("candidate_id") or ""): item
        for item in (evaluations or [])
        if isinstance(item, dict) and item.get("candidate_id")
    }
    result = []
    for candidate in candidates:
        merged = dict(candidate)
        evaluation = evaluation_map.get(candidate["candidate_id"]) or {}
        for field in (
            "thesis_alignment", "factuality", "information_gain", "cover_readability",
            "restraint", "landscape_score", "vertical_score",
        ):
            try:
                merged[field] = max(0, min(100, int(float(evaluation.get(field, 0)))))
            except (TypeError, ValueError):
                merged[field] = 0
        merged["reason"] = _clean_text(evaluation.get("reason") or "")[:500]
        merged["rejection_reason"] = _clean_text(evaluation.get("rejection_reason") or "")[:500]
        result.append(merged)
    return result


def _select_reviewed_titles(
    candidates: List[Dict[str, Any]],
    review_payload: Dict[str, Any],
) -> Dict[str, Dict[str, Any]]:
    by_id = {item["candidate_id"]: item for item in candidates}
    original = by_id.get("original_pdf")

    def select(kind: str, score_field: str) -> Optional[Dict[str, Any]]:
        requested = by_id.get(str(review_payload.get(f"{kind}_candidate_id") or ""))
        ranked = sorted(candidates, key=lambda item: item.get(score_field, 0), reverse=True)
        chosen = requested or (ranked[0] if ranked else None)
        if (
            chosen and original and chosen["candidate_id"] != "original_pdf"
            and chosen.get(score_field, 0) < original.get(score_field, 0) + 3
        ):
            chosen = original
        return dict(chosen) if chosen else None

    landscape = select("landscape", "landscape_score")
    vertical = select("vertical", "vertical_score")
    if landscape and vertical and _review_title_key(landscape) == _review_title_key(vertical):
        ranked_vertical = sorted(candidates, key=lambda item: item.get("vertical_score", 0), reverse=True)
        distinct_vertical = next(
            (
                item for item in ranked_vertical
                if _review_title_key(item) != _review_title_key(landscape)
                and (
                    not original
                    or item["candidate_id"] == "original_pdf"
                    or item.get("vertical_score", 0) >= original.get("vertical_score", 0) + 3
                )
            ),
            None,
        )
        if distinct_vertical:
            vertical = dict(distinct_vertical)
    backup_id = str(review_payload.get("backup_candidate_id") or "")
    backup = by_id.get(backup_id)
    if not backup:
        selected_ids = {
            item.get("candidate_id") for item in (landscape, vertical) if isinstance(item, dict)
        }
        backup = next(
            (item for item in sorted(
                candidates,
                key=lambda candidate: max(
                    candidate.get("landscape_score", 0),
                    candidate.get("vertical_score", 0),
                ),
                reverse=True,
            ) if item["candidate_id"] not in selected_ids),
            None,
        )
    return {
        key: value for key, value in {
            "landscape": landscape,
            "vertical": vertical,
            "backup": dict(backup) if backup else None,
        }.items() if value
    }


def _review_title_key(candidate: Dict[str, Any]) -> str:
    title = _clean_multiline_text((candidate or {}).get("title") or "")
    return re.sub(r"[\s：:｜|/／\-—_]+", "", title).lower()


def _normalize_existing_title_review(structured: Dict[str, Any]) -> Dict[str, Any]:
    """Apply current selection invariants to already-reviewed, cached results."""
    result = dict(structured or {})
    review = dict(result.get("title_review") or {})
    candidates = [dict(item) for item in (review.get("candidates") or []) if isinstance(item, dict)]
    if not candidates:
        return result

    previous = review.get("selected") if isinstance(review.get("selected"), dict) else {}
    review_payload = {
        f"{kind}_candidate_id": str((previous.get(kind) or {}).get("candidate_id") or "")
        for kind in ("landscape", "vertical", "backup")
    }
    selected = _select_reviewed_titles(candidates, review_payload)
    landscape = selected.get("landscape") or {}
    vertical = selected.get("vertical") or {}
    if landscape.get("title"):
        result["landscape_title"] = landscape["title"]
        result["landscape_subtitle"] = landscape.get("subtitle") or ""
        result["cover_title"] = landscape["title"]
        result["cover_subtitle"] = landscape.get("subtitle") or ""
    if vertical.get("title"):
        result["vertical_title"] = vertical["title"]
        result["vertical_subtitle"] = vertical.get("subtitle") or ""
    review["selected"] = selected
    result["title_review"] = review
    return result


def _title_context(structured: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "title": structured.get("title"),
        "summary": structured.get("summary"),
        "landscape_title": structured.get("landscape_title") or structured.get("cover_title"),
        "landscape_subtitle": structured.get("landscape_subtitle") or structured.get("cover_subtitle"),
        "vertical_title": structured.get("vertical_title"),
        "vertical_subtitle": structured.get("vertical_subtitle"),
        "titles": structured.get("titles"),
    }


def _pdf_title_baseline(pdf_filename: str, pdf_topic_title: str = "") -> str:
    filename = Path(str(pdf_filename or "")).stem.strip()
    filename = re.sub(
        r"[-_（(]?(?:(?:20)?\d{2}[01]\d[0-3]\d|20\d{2}[-_.年]\d{1,2}[-_.月]\d{1,2}日?)[）)]?$",
        "",
        filename,
    ).strip(" -_")
    topic = _clean_text(pdf_topic_title or "")
    generic_topics = {"外资研报", "#外资研报", "研报", "#研报"}
    return _clean_text(filename or ("" if topic in generic_topics else topic))[:300]


def _deepseek_normalize(
    raw_text: str,
    *,
    initial_text: str,
    metadata: Dict[str, Any],
    timeout_seconds: Optional[int] = None,
    minimum_script_chars: int = 0,
) -> Optional[Dict[str, Any]]:
    api_key = os.environ.get("REPORT_AUDIO_NORMALIZER_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        return None

    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    model = _normalizer_model()
    timeout = _normalizer_timeout(timeout_seconds)
    try:
        minimum = max(0, min(20000, int(minimum_script_chars)))
    except (TypeError, ValueError):
        minimum = 0
    system_prompt = (
        "你是内容流水线的结构化整理器。只做抽取和规整，不新增事实或观点，"
        "同时不得把已有的关键背景、数据、逻辑链条、分歧和风险压缩丢失。"
        "你必须输出严格 JSON，不要 Markdown，不要解释。"
    )
    script_length_directive = (
        f"final_script_for_tts 清理后的中文可朗读正文必须不少于 {minimum} 字。"
        "如需补足长度，只能从下方 Gemini 结果中完整保留和组织已有事实、数据、解释、分歧与风险，"
        "禁止重复句凑字或新增材料中不存在的结论。"
        if minimum > 0
        else ""
    )
    user_prompt = "\n".join(filter(None, [
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
        script_length_directive,
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
    ]))

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


def _validate_structured_result(
    payload: Dict[str, Any],
    raw_text: str,
    *,
    minimum_script_chars: int = 0,
) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {}
    script = clean_final_script_for_tts(payload.get("final_script_for_tts") or "")
    if not script:
        script = clean_final_script_for_tts(_extract_final_script(raw_text))
    if not script:
        raise ReportAudioWorkflowError("结构化结果缺少 final_script_for_tts")
    assert_final_tts_text_clean(script)
    quality_issue = _tts_script_quality_issue(script)
    if quality_issue:
        raise ReportAudioWorkflowError(f"结构化结果不可用于朗读: {quality_issue}")
    try:
        minimum = max(0, min(20000, int(minimum_script_chars)))
    except (TypeError, ValueError):
        minimum = 0
    script_chars = len(re.sub(r"\s+", "", script))
    if script_chars < minimum:
        raise ReportAudioWorkflowError(
            f"结构化可朗读正文过短: {script_chars} 字，低于 {minimum} 字"
        )

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
    if payload.get("schema_version") == "report_audio_gem_v2":
        for key in (
            "schema_version",
            "language",
            "output_mode",
            "source_files",
            "metadata",
            "material_boundary",
            "core_analysis",
            "fact_ledger",
            "paragraph_map",
            "title_candidates",
            "selected_titles",
            "evidence_map",
            "quality_control",
            "batch_quality_control",
        ):
            if key in payload:
                result[key] = copy.deepcopy(payload[key])
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
    start = text.find("{")
    if start >= 0:
        depth = 0
        in_string = False
        escaped = False
        for index in range(start, len(text)):
            char = text[index]
            if escaped:
                escaped = False
                continue
            if char == "\\" and in_string:
                escaped = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    text = text[start:index + 1]
                    break
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
    text = _strip_tts_citations(text)
    text = _strip_leading_style_fragments(text)
    text = _strip_tts_structure_labels(text)
    return _strip_trailing_section_stub(_clean_text(text))


def _strip_tts_citations(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""

    citation = r"cite\s*[:：]\s*\d+(?:\s*(?:[-–—,，、]|\band\b)\s*\d+)*"
    # DeepSeek/Gemini may emit the same citation with ASCII or Chinese wrappers,
    # or leave it bare inside a sentence. None of these markers should be read.
    text = re.sub(
        rf"[\[\(（【]\s*{citation}\s*[\]\)）】]",
        "",
        text,
        flags=re.I,
    )
    text = re.sub(rf"(?<![A-Za-z0-9_]){citation}(?![A-Za-z0-9_])", " ", text, flags=re.I)
    return _clean_text(text)


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
    normalized = str(value or "").replace("\\r\\n", "\n").replace("\\n", "\n").replace("\r", "\n")
    lines = [_clean_text(line) for line in normalized.split("\n")]
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
    return os.environ.get("REPORT_AUDIO_NORMALIZER_MODEL") or os.environ.get("DEEPSEEK_MODEL") or "deepseek-v4-flash"


def _title_generation_model() -> str:
    return (
        os.environ.get("REPORT_AUDIO_TITLE_GENERATION_MODEL")
        or os.environ.get("REPORT_AUDIO_TITLE_REVIEW_MODEL")
        or os.environ.get("DEEPSEEK_PRO_MODEL")
        or "deepseek-v4-pro"
    )


def _title_review_model() -> str:
    return (
        os.environ.get("REPORT_AUDIO_TITLE_REVIEW_MODEL")
        or os.environ.get("REPORT_AUDIO_TITLE_GENERATION_MODEL")
        or os.environ.get("DEEPSEEK_PRO_MODEL")
        or "deepseek-v4-pro"
    )


def _title_review_timeout(timeout_seconds: Optional[int] = None) -> int:
    if timeout_seconds is not None:
        try:
            return max(1, int(timeout_seconds))
        except (TypeError, ValueError):
            pass
    return _int_env("REPORT_AUDIO_TITLE_REVIEW_TIMEOUT", 120)


def _project_root() -> str:
    return str(Path(__file__).resolve().parents[3])
