#!/usr/bin/env python3
"""Minimal Doubao Seed-TTS WebSocket V3 client."""

from __future__ import annotations

import json
import os
import struct
import uuid
from dataclasses import dataclass, field
from typing import Any

try:
    import websocket
except ImportError as exc:  # pragma: no cover - environment dependent
    raise RuntimeError(
        "Missing websocket-client. Run with a Python environment that provides the "
        "'websocket' module."
    ) from exc

EVENT_START_CONNECTION = 1
EVENT_FINISH_CONNECTION = 2
EVENT_CONNECTION_STARTED = 50
EVENT_CONNECTION_FAILED = 51
EVENT_START_SESSION = 100
EVENT_FINISH_SESSION = 102
EVENT_SESSION_STARTED = 150
EVENT_SESSION_CANCELED = 151
EVENT_SESSION_FINISHED = 152
EVENT_SESSION_FAILED = 153
EVENT_TASK_REQUEST = 200
EVENT_TTS_RESPONSE = 352

MESSAGE_TYPE_FULL_CLIENT_REQUEST = 0x1
MESSAGE_TYPE_AUDIO_ONLY_RESPONSE = 0xB
MESSAGE_TYPE_ERROR = 0xF
FLAG_WITH_EVENT = 0x4
SERIALIZATION_JSON = 0x1


class DoubaoError(RuntimeError):
    pass


@dataclass
class DoubaoResult:
    audio: bytes = b""
    session_id: str = ""
    events: list[dict[str, Any]] = field(default_factory=list)


def _api_key() -> str:
    value = (
        os.getenv("DOUBAO_TTS_API_KEY")
        or os.getenv("VOLCENGINE_TTS_API_KEY")
        or os.getenv("VOLC_API_KEY")
        or os.getenv("X_API_KEY")
    )
    if not value:
        raise DoubaoError("Missing DOUBAO_TTS_API_KEY")
    return value


def _header(message_type: int, flags: int, serialization: int) -> bytes:
    return bytes(
        [
            0x11,
            ((message_type & 0x0F) << 4) | (flags & 0x0F),
            ((serialization & 0x0F) << 4),
            0,
        ]
    )


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode()


def _pack_event(event: int, payload: dict[str, Any]) -> bytes:
    body = _json_bytes(payload)
    return (
        _header(MESSAGE_TYPE_FULL_CLIENT_REQUEST, FLAG_WITH_EVENT, SERIALIZATION_JSON)
        + struct.pack(">iI", event, len(body))
        + body
    )


def _pack_session(session_id: str, event: int, payload: dict[str, Any]) -> bytes:
    session = session_id.encode()
    body = _json_bytes(payload)
    return (
        _header(MESSAGE_TYPE_FULL_CLIENT_REQUEST, FLAG_WITH_EVENT, SERIALIZATION_JSON)
        + struct.pack(">iI", event, len(session))
        + session
        + struct.pack(">I", len(body))
        + body
    )


def _read_i32(data: bytes, offset: int) -> tuple[int, int]:
    if offset + 4 > len(data):
        raise DoubaoError("Short response frame")
    return struct.unpack(">i", data[offset : offset + 4])[0], offset + 4


def _read_u32(data: bytes, offset: int) -> tuple[int, int]:
    if offset + 4 > len(data):
        raise DoubaoError("Short response frame")
    return struct.unpack(">I", data[offset : offset + 4])[0], offset + 4


def _read_bytes(data: bytes, offset: int) -> tuple[bytes, int]:
    size, offset = _read_u32(data, offset)
    if offset + size > len(data):
        raise DoubaoError("Invalid response payload length")
    return data[offset : offset + size], offset + size


def _decode_json(raw: bytes) -> dict[str, Any]:
    if not raw:
        return {}
    try:
        return json.loads(raw.decode())
    except Exception:
        return {"raw": raw.decode(errors="replace")}


def _parse_frame(data: bytes | str) -> dict[str, Any]:
    if isinstance(data, str):
        return {"event": None, "payload": {"message": data}, "audio": b""}
    if len(data) < 4:
        raise DoubaoError("Response frame is too short")
    message_type = data[1] >> 4
    flags = data[1] & 0x0F
    offset = (data[0] & 0x0F) * 4
    frame: dict[str, Any] = {
        "message_type": message_type,
        "event": None,
        "payload": {},
        "audio": b"",
    }
    if message_type == MESSAGE_TYPE_ERROR:
        code, offset = _read_i32(data, offset)
        raw, _ = _read_bytes(data, offset)
        payload = _decode_json(raw)
        raise DoubaoError(payload.get("message") or f"Doubao error {code}")
    if flags == FLAG_WITH_EVENT:
        frame["event"], offset = _read_i32(data, offset)
    event = frame["event"]
    if event in {EVENT_CONNECTION_STARTED, EVENT_CONNECTION_FAILED}:
        _, offset = _read_bytes(data, offset)
        if offset < len(data):
            raw, _ = _read_bytes(data, offset)
            frame["payload"] = _decode_json(raw)
        return frame
    if event in {
        EVENT_SESSION_STARTED,
        EVENT_SESSION_CANCELED,
        EVENT_SESSION_FINISHED,
        EVENT_SESSION_FAILED,
        EVENT_TTS_RESPONSE,
    } or message_type == MESSAGE_TYPE_AUDIO_ONLY_RESPONSE:
        _, offset = _read_bytes(data, offset)
        raw, _ = _read_bytes(data, offset)
        if message_type == MESSAGE_TYPE_AUDIO_ONLY_RESPONSE or event == EVENT_TTS_RESPONSE:
            frame["audio"] = raw
        else:
            frame["payload"] = _decode_json(raw)
        return frame
    if offset < len(data):
        raw, _ = _read_bytes(data, offset)
        frame["payload"] = _decode_json(raw)
    return frame


def _speech_rate(value: float) -> int:
    return max(-50, min(100, round((float(value) - 1.0) * 100)))


class DoubaoClient:
    def __init__(self, timeout: int = 90):
        self.api_key = _api_key()
        self.resource_id = os.getenv("DOUBAO_TTS_RESOURCE_ID", "seed-tts-2.0")
        self.endpoint = os.getenv(
            "DOUBAO_TTS_WS_URL",
            "wss://openspeech.bytedance.com/api/v3/tts/bidirection",
        )
        self.timeout = timeout

    def synthesize(
        self,
        text: str,
        voice_id: str,
        *,
        model: str,
        sample_rate: int,
        bit_rate: int,
        speed: float,
        loudness: float,
        emotion: str = "",
        emotion_scale: int = 4,
        uid: str = "codex_skill",
    ) -> DoubaoResult:
        if not text.strip():
            raise ValueError("Synthesis text is empty")
        session_id = str(uuid.uuid4())
        audio_params: dict[str, Any] = {
            "format": "mp3",
            "sample_rate": sample_rate,
            "bit_rate": bit_rate,
            "speech_rate": _speech_rate(speed),
            "loudness_rate": _speech_rate(loudness),
            "enable_subtitle": False,
        }
        if emotion:
            audio_params["emotion"] = emotion
            audio_params["emotion_scale"] = max(1, min(5, emotion_scale))
        session_payload = {
            "user": {"uid": uid},
            "event": EVENT_START_SESSION,
            "namespace": "BidirectionalTTS",
            "req_params": {
                "speaker": voice_id,
                "model": model,
                "audio_params": audio_params,
                "additions": json.dumps(
                    {
                        "disable_markdown_filter": True,
                        "enable_language_detector": True,
                    },
                    ensure_ascii=False,
                ),
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
        ws = websocket.create_connection(
            self.endpoint, header=headers, timeout=self.timeout
        )
        result = DoubaoResult(session_id=session_id)
        try:
            ws.send_binary(_pack_event(EVENT_START_CONNECTION, {}))
            self._wait_for(ws, EVENT_CONNECTION_STARTED, result)
            ws.send_binary(
                _pack_session(
                    session_id, EVENT_START_SESSION, session_payload
                )
            )
            self._wait_for(ws, EVENT_SESSION_STARTED, result)
            ws.send_binary(
                _pack_session(session_id, EVENT_TASK_REQUEST, task_payload)
            )
            ws.send_binary(
                _pack_session(session_id, EVENT_FINISH_SESSION, {})
            )
            while True:
                frame = self._recv(ws)
                result.events.append({"event": frame.get("event")})
                result.audio += frame.get("audio") or b""
                if frame.get("event") in {
                    EVENT_SESSION_FINISHED,
                    EVENT_SESSION_CANCELED,
                }:
                    break
            ws.send_binary(_pack_event(EVENT_FINISH_CONNECTION, {}))
        finally:
            ws.close()
        if not result.audio:
            raise DoubaoError("Doubao returned no audio")
        return result

    def _recv(self, ws: Any) -> dict[str, Any]:
        frame = _parse_frame(ws.recv())
        if frame.get("event") in {
            EVENT_CONNECTION_FAILED,
            EVENT_SESSION_FAILED,
        }:
            payload = frame.get("payload") or {}
            raise DoubaoError(
                payload.get("message")
                or f"Doubao event failed: {frame.get('event')}"
            )
        return frame

    def _wait_for(
        self, ws: Any, expected: int, result: DoubaoResult
    ) -> dict[str, Any]:
        while True:
            frame = self._recv(ws)
            result.events.append({"event": frame.get("event")})
            if frame.get("event") == expected:
                return frame

