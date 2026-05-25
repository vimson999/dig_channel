import json
import logging
import os
import struct
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import websocket

logger = logging.getLogger(__name__)


EVENT_START_CONNECTION = 1
EVENT_FINISH_CONNECTION = 2
EVENT_CONNECTION_STARTED = 50
EVENT_CONNECTION_FAILED = 51
EVENT_CONNECTION_FINISHED = 52
EVENT_START_SESSION = 100
EVENT_CANCEL_SESSION = 101
EVENT_FINISH_SESSION = 102
EVENT_SESSION_STARTED = 150
EVENT_SESSION_CANCELED = 151
EVENT_SESSION_FINISHED = 152
EVENT_SESSION_FAILED = 153
EVENT_TASK_REQUEST = 200
EVENT_TTS_SENTENCE_START = 350
EVENT_TTS_SENTENCE_END = 351
EVENT_TTS_RESPONSE = 352

MESSAGE_TYPE_FULL_CLIENT_REQUEST = 0x1
MESSAGE_TYPE_FULL_SERVER_RESPONSE = 0x9
MESSAGE_TYPE_AUDIO_ONLY_RESPONSE = 0xB
MESSAGE_TYPE_ERROR = 0xF

FLAG_WITH_EVENT = 0x4
SERIALIZATION_RAW = 0x0
SERIALIZATION_JSON = 0x1


class DoubaoTTSConfigError(RuntimeError):
    pass


class DoubaoTTSProtocolError(RuntimeError):
    pass


@dataclass
class DoubaoTTSResult:
    audio: bytes = b""
    events: List[Dict[str, Any]] = field(default_factory=list)
    subtitles: List[Dict[str, Any]] = field(default_factory=list)
    session_id: str = ""


def is_configured() -> bool:
    return bool(_get_api_key())


def _get_api_key() -> Optional[str]:
    return (
        os.getenv("DOUBAO_TTS_API_KEY")
        or os.getenv("VOLCENGINE_TTS_API_KEY")
        or os.getenv("VOLC_API_KEY")
        or os.getenv("X_API_KEY")
    )


def _get_endpoint() -> str:
    return os.getenv(
        "DOUBAO_TTS_WS_URL",
        "wss://openspeech.bytedance.com/api/v3/tts/bidirection",
    )


def _json_bytes(payload: Dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _header(message_type: int, flags: int, serialization: int) -> bytes:
    return bytes([
        0x11,
        ((message_type & 0x0F) << 4) | (flags & 0x0F),
        ((serialization & 0x0F) << 4),
        0x00,
    ])


def _pack_event_payload(event: int, payload: Dict[str, Any]) -> bytes:
    body = _json_bytes(payload)
    return (
        _header(MESSAGE_TYPE_FULL_CLIENT_REQUEST, FLAG_WITH_EVENT, SERIALIZATION_JSON)
        + struct.pack(">iI", event, len(body))
        + body
    )


def _pack_session_payload(session_id: str, event: int, payload: Dict[str, Any]) -> bytes:
    session = session_id.encode("utf-8")
    body = _json_bytes(payload)
    return (
        _header(MESSAGE_TYPE_FULL_CLIENT_REQUEST, FLAG_WITH_EVENT, SERIALIZATION_JSON)
        + struct.pack(">iI", event, len(session))
        + session
        + struct.pack(">I", len(body))
        + body
    )


def _read_u32(data: bytes, offset: int) -> Tuple[int, int]:
    if offset + 4 > len(data):
        raise DoubaoTTSProtocolError("响应帧长度不足")
    return struct.unpack(">I", data[offset:offset + 4])[0], offset + 4


def _read_i32(data: bytes, offset: int) -> Tuple[int, int]:
    if offset + 4 > len(data):
        raise DoubaoTTSProtocolError("响应帧长度不足")
    return struct.unpack(">i", data[offset:offset + 4])[0], offset + 4


def _read_bytes(data: bytes, offset: int) -> Tuple[bytes, int]:
    size, offset = _read_u32(data, offset)
    if offset + size > len(data):
        raise DoubaoTTSProtocolError("响应帧 payload 长度不足")
    return data[offset:offset + size], offset + size


def _decode_json(raw: bytes) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {"raw": raw.decode("utf-8", errors="replace")}


def parse_frame(data: bytes) -> Dict[str, Any]:
    if isinstance(data, str):
        return {"message_type": "text", "payload": {"message": data}}
    if len(data) < 4:
        raise DoubaoTTSProtocolError("响应帧过短")

    message_type = data[1] >> 4
    flags = data[1] & 0x0F
    serialization = data[2] >> 4
    offset = (data[0] & 0x0F) * 4
    frame: Dict[str, Any] = {
        "message_type": message_type,
        "flags": flags,
        "serialization": serialization,
        "event": None,
        "connection_id": "",
        "session_id": "",
        "payload": {},
        "audio": b"",
        "error_code": None,
    }

    if message_type == MESSAGE_TYPE_ERROR:
        error_code, offset = _read_i32(data, offset)
        payload_raw, _ = _read_bytes(data, offset)
        frame["error_code"] = error_code
        frame["payload"] = _decode_json(payload_raw)
        return frame

    if flags == FLAG_WITH_EVENT:
        event, offset = _read_i32(data, offset)
        frame["event"] = event

    if frame["event"] in {
        EVENT_CONNECTION_STARTED,
        EVENT_CONNECTION_FAILED,
        EVENT_CONNECTION_FINISHED,
    }:
        connection_raw, offset = _read_bytes(data, offset)
        frame["connection_id"] = connection_raw.decode("utf-8", errors="replace")
        if offset < len(data):
            payload_raw, offset = _read_bytes(data, offset)
            frame["payload"] = _decode_json(payload_raw)
        return frame

    if frame["event"] in {
        EVENT_SESSION_STARTED,
        EVENT_SESSION_CANCELED,
        EVENT_SESSION_FINISHED,
        EVENT_SESSION_FAILED,
        EVENT_TTS_SENTENCE_START,
        EVENT_TTS_SENTENCE_END,
        EVENT_TTS_RESPONSE,
    } or message_type == MESSAGE_TYPE_AUDIO_ONLY_RESPONSE:
        session_raw, offset = _read_bytes(data, offset)
        frame["session_id"] = session_raw.decode("utf-8", errors="replace")
        payload_raw, offset = _read_bytes(data, offset)
        if message_type == MESSAGE_TYPE_AUDIO_ONLY_RESPONSE or frame["event"] == EVENT_TTS_RESPONSE:
            frame["audio"] = payload_raw
        else:
            frame["payload"] = _decode_json(payload_raw)
        return frame

    if offset < len(data):
        payload_raw, _ = _read_bytes(data, offset)
        frame["payload"] = _decode_json(payload_raw) if serialization == SERIALIZATION_JSON else {"raw": payload_raw}
    return frame


def _speech_rate_from_speed(speed_ratio: float) -> int:
    try:
        speed = float(speed_ratio)
    except (TypeError, ValueError):
        speed = 1.0
    return max(-50, min(100, round((speed - 1.0) * 100)))


class DoubaoTTSWebSocketClient:
    def __init__(
        self,
        api_key: Optional[str] = None,
        resource_id: Optional[str] = None,
        endpoint: Optional[str] = None,
        timeout: int = 60,
    ):
        self.api_key = api_key or _get_api_key()
        self.resource_id = resource_id or os.getenv("DOUBAO_TTS_RESOURCE_ID", "seed-tts-2.0")
        self.endpoint = endpoint or _get_endpoint()
        self.timeout = timeout
        if not self.api_key:
            raise DoubaoTTSConfigError("缺少 DOUBAO_TTS_API_KEY / VOLCENGINE_TTS_API_KEY / VOLC_API_KEY")

    def synthesize(
        self,
        text: str,
        speaker: str,
        *,
        model: str = "seed-tts-2.0-standard",
        audio_format: str = "mp3",
        sample_rate: int = 24000,
        bit_rate: Optional[int] = 128000,
        speed_ratio: float = 1.0,
        loudness_ratio: float = 1.0,
        emotion: str = "",
        emotion_scale: int = 4,
        enable_subtitle: bool = True,
        uid: str = "system_user",
    ) -> DoubaoTTSResult:
        text = (text or "").strip()
        if not text:
            raise ValueError("合成文本不能为空")
        if not speaker:
            raise ValueError("音色 ID 不能为空")

        session_id = str(uuid.uuid4())
        audio_params: Dict[str, Any] = {
            "format": audio_format,
            "sample_rate": int(sample_rate),
            "speech_rate": _speech_rate_from_speed(speed_ratio),
            "loudness_rate": _speech_rate_from_speed(loudness_ratio),
            "enable_subtitle": bool(enable_subtitle),
        }
        if bit_rate:
            audio_params["bit_rate"] = int(bit_rate)
        if emotion:
            audio_params["emotion"] = emotion
            audio_params["emotion_scale"] = max(1, min(5, int(emotion_scale or 4)))

        session_payload = {
            "user": {"uid": uid},
            "event": EVENT_START_SESSION,
            "namespace": "BidirectionalTTS",
            "req_params": {
                "speaker": speaker,
                "model": model,
                "audio_params": audio_params,
                "additions": json.dumps({
                    "disable_markdown_filter": True,
                    "enable_language_detector": True,
                }, ensure_ascii=False),
            },
        }
        task_payload = {
            "user": {"uid": uid},
            "event": EVENT_TASK_REQUEST,
            "namespace": "BidirectionalTTS",
            "req_params": {"text": text},
        }

        headers = [
            f"X-Api-Key: {self.api_key}",
            f"X-Api-Resource-Id: {self.resource_id}",
            f"X-Api-Connect-Id: {uuid.uuid4()}",
        ]

        ws = websocket.create_connection(self.endpoint, header=headers, timeout=self.timeout)
        result = DoubaoTTSResult(session_id=session_id)
        try:
            ws.send_binary(_pack_event_payload(EVENT_START_CONNECTION, {}))
            self._wait_for(ws, EVENT_CONNECTION_STARTED, result)

            ws.send_binary(_pack_session_payload(session_id, EVENT_START_SESSION, session_payload))
            self._wait_for(ws, EVENT_SESSION_STARTED, result)

            ws.send_binary(_pack_session_payload(session_id, EVENT_TASK_REQUEST, task_payload))
            ws.send_binary(_pack_session_payload(session_id, EVENT_FINISH_SESSION, {}))
            self._collect_until_session_finished(ws, result)

            ws.send_binary(_pack_event_payload(EVENT_FINISH_CONNECTION, {}))
        finally:
            ws.close()
        return result

    def _recv_frame(self, ws) -> Dict[str, Any]:
        frame = parse_frame(ws.recv())
        if frame.get("message_type") == MESSAGE_TYPE_ERROR:
            payload = frame.get("payload") or {}
            raise DoubaoTTSProtocolError(payload.get("message") or f"豆包 TTS 错误: {frame.get('error_code')}")
        if frame.get("event") in {EVENT_CONNECTION_FAILED, EVENT_SESSION_FAILED}:
            payload = frame.get("payload") or {}
            raise DoubaoTTSProtocolError(payload.get("message") or f"豆包 TTS 事件失败: {frame.get('event')}")
        return frame

    def _wait_for(self, ws, expected_event: int, result: DoubaoTTSResult) -> Dict[str, Any]:
        while True:
            frame = self._recv_frame(ws)
            result.events.append(_public_event(frame))
            if frame.get("event") == expected_event:
                return frame

    def _collect_until_session_finished(self, ws, result: DoubaoTTSResult) -> None:
        while True:
            frame = self._recv_frame(ws)
            result.events.append(_public_event(frame))
            if frame.get("audio"):
                result.audio += frame["audio"]
            payload = frame.get("payload") or {}
            if _looks_like_subtitle(payload):
                result.subtitles.append(payload)
            if frame.get("event") in {EVENT_SESSION_FINISHED, EVENT_SESSION_CANCELED}:
                return


def _looks_like_subtitle(payload: Dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    if payload.get("words") or payload.get("text"):
        return True
    return any(isinstance(v, dict) and (v.get("words") or v.get("text")) for v in payload.values())


def _public_event(frame: Dict[str, Any]) -> Dict[str, Any]:
    payload = frame.get("payload")
    return {
        "event": frame.get("event"),
        "message_type": frame.get("message_type"),
        "session_id": frame.get("session_id"),
        "connection_id": frame.get("connection_id"),
        "payload": payload if payload else {},
        "audio_bytes": len(frame.get("audio") or b""),
    }


def write_srt(subtitles: List[Dict[str, Any]], output_path: str) -> bool:
    cues: List[Tuple[float, float, str]] = []
    for item in subtitles:
        payloads = [item]
        payloads.extend(v for v in item.values() if isinstance(v, dict))
        for payload in payloads:
            text = str(payload.get("text") or "").strip()
            words = payload.get("words") or []
            if not text and words:
                text = "".join(str(w.get("word") or "") for w in words).strip()
            if not text:
                continue
            starts = [float(w.get("startTime")) for w in words if w.get("startTime") is not None]
            ends = [float(w.get("endTime")) for w in words if w.get("endTime") is not None]
            if starts and ends:
                cues.append((min(starts), max(ends), text))

    if not cues:
        return False

    def fmt(seconds: float) -> str:
        milliseconds = int(round(max(seconds, 0) * 1000))
        h, rem = divmod(milliseconds, 3600_000)
        m, rem = divmod(rem, 60_000)
        s, ms = divmod(rem, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for index, (start, end, text) in enumerate(sorted(cues), 1):
        lines.extend([str(index), f"{fmt(start)} --> {fmt(end)}", text, ""])
    Path(output_path).write_text("\n".join(lines), encoding="utf-8")
    return True
