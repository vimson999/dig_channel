import sys
from pathlib import Path
from types import SimpleNamespace


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from api.tts_v2 import _segment_to_public, _task_to_list_item  # noqa: E402


def _task(audio_path):
    return SimpleNamespace(
        id="task-1",
        feature="doubao_tts",
        params={"tts": {"title": "demo", "config": {}, "segments": ["text"]}},
        result={"segments": [{
            "sequence": 1,
            "status": "success",
            "audio_path": str(audio_path),
            "subtitle_path": None,
        }]},
        status="completed",
        created_at=None,
        updated_at=None,
    )


def test_history_does_not_advertise_retained_path_after_file_is_removed(tmp_path):
    missing = tmp_path / "removed.mp3"

    item = _task_to_list_item(_task(missing))

    assert item["success_count"] == 1
    assert item["first_audio_segment"] is None


def test_public_segment_only_exposes_existing_asset_url(tmp_path):
    audio = tmp_path / "available.mp3"
    audio.write_bytes(b"audio")
    available = _segment_to_public("task-1", {
        "sequence": 1,
        "status": "success",
        "audio_path": str(audio),
    })
    missing = _segment_to_public("task-1", {
        "sequence": 2,
        "status": "success",
        "audio_path": str(tmp_path / "removed.mp3"),
    })

    assert available["audio_url"].endswith("/segment/1/audio")
    assert "audio_url" not in missing
    assert "audio_path" not in missing
