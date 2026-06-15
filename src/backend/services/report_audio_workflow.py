import json
import os
import re
import wave
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests


FEATURE_NAME = "report_pdf_audio_workflow"
QUEUE_NAME = "report_audio_workflow"

DEFAULT_INITIAL_PROMPT = (
    "请解析上传的研报 PDF，提取核心观点、关键数据、风险提示和可用于口播的摘要。"
)
DEFAULT_REVIEW_PROMPT = (
    "担任首席责任主编，利用提供的PDF材料和可查网络信息，对上文内容逐条复核与勘误，"
    "确认事实准确、逻辑清晰、表达规范，列出每条修正点及依据，并给出最终优化后的信息版本，"
    "附上核对过程说明和参考来源。"
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


def normalize_for_tts(raw_text: str, *, initial_text: str = "", metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    raw_text = (raw_text or "").strip()
    if not raw_text:
        raise ReportAudioWorkflowError("Gemini 结果为空，无法结构化")

    payload = _deepseek_normalize(raw_text, initial_text=initial_text, metadata=metadata or {})
    if not payload:
        payload = _fallback_normalize(raw_text)
    return _validate_structured_result(payload, raw_text)


def split_tts_text(text: str, max_chars: Optional[int] = None) -> List[str]:
    text = _clean_text(text)
    if not text:
        return []
    max_chars = max_chars or _int_env("REPORT_AUDIO_TTS_CHUNK_CHARS", _int_env("DOUBAO_BROWSER_TTS_MAX_TEXT_LENGTH", 4500))
    max_chars = max(500, min(max_chars, 5000))
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


def status_payload() -> Dict[str, Any]:
    return {
        "configured": True,
        "deepseek_configured": bool(os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("REPORT_AUDIO_NORMALIZER_API_KEY")),
        "normalizer_provider": "deepseek" if os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("REPORT_AUDIO_NORMALIZER_API_KEY") else "local_fallback",
        "mode": "celery_orchestrator",
    }


def _deepseek_normalize(raw_text: str, *, initial_text: str, metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    api_key = os.environ.get("REPORT_AUDIO_NORMALIZER_API_KEY") or os.environ.get("DEEPSEEK_API_KEY")
    if not api_key:
        return None

    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    model = os.environ.get("REPORT_AUDIO_NORMALIZER_MODEL") or os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
    timeout = _int_env("REPORT_AUDIO_NORMALIZER_TIMEOUT", 120)
    system_prompt = (
        "你是内容流水线的结构化整理器。只做抽取和规整，不新增事实，不扩写观点。"
        "你必须输出严格 JSON，不要 Markdown，不要解释。"
    )
    user_prompt = "\n".join([
        "请把下面 Gemini 审核后的研报口播结果整理成严格 JSON。",
        "字段必须包含：",
        "- title: 字符串，最适合最终音频/内容的标题",
        "- summary: 字符串，200字以内摘要",
        "- final_script_for_tts: 字符串，只包含要交给 TTS 朗读的最终口播正文，不包含修正清单、标题方案、参考来源说明、Markdown 标题",
        "- corrections: 数组，每项包含 issue、basis、action",
        "- titles: 对象，包含 standard 和 short 两个数组",
        "- sources: 数组，引用来源说明",
        "- risk_notes: 数组，风险提示或合规提示",
        "- editor_notes: 字符串，简短说明整理策略",
        "如果原文中没有某字段内容，用空字符串或空数组。",
        "final_script_for_tts 必须适合直接朗读，保留必要的投资风险提示。",
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
    content = response.json()["choices"][0]["message"]["content"]
    payload = _json_from_text(content)
    if isinstance(payload, dict):
        payload["provider"] = "deepseek"
    return payload


def _validate_structured_result(payload: Dict[str, Any], raw_text: str) -> Dict[str, Any]:
    if not isinstance(payload, dict):
        payload = {}
    script = _clean_text(payload.get("final_script_for_tts") or "")
    if not script:
        script = _extract_final_script(raw_text)
    if not script:
        raise ReportAudioWorkflowError("结构化结果缺少 final_script_for_tts")

    result = {
        "title": _clean_text(payload.get("title") or "")[:300],
        "summary": _clean_text(payload.get("summary") or "")[:1000],
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
    return {
        "title": "",
        "summary": "",
        "final_script_for_tts": _extract_final_script(raw_text) or raw_text,
        "corrections": [],
        "titles": {"standard": [], "short": []},
        "sources": [],
        "risk_notes": [],
        "editor_notes": "未配置 DeepSeek，使用本地规则抽取最终口播正文。",
        "provider": "local_fallback",
    }


def _extract_final_script(raw_text: str) -> str:
    text = _clean_text(raw_text)
    markers = [
        "最终优化版口播文案",
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

    end_markers = ["标题与包装方案", "标准版标题方案", "16字以内", "核对过程说明"]
    end_positions = [text.find(marker) for marker in end_markers if text.find(marker) > 200]
    if end_positions:
        text = text[:min(end_positions)].strip()
    return text


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


def _project_root() -> str:
    return str(Path(__file__).resolve().parents[3])
