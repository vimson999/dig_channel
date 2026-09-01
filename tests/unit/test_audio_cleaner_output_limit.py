import json
from pathlib import Path

from services.content_pipeline.audio_cleaner_adapter import (
    AudioCleanOptions,
    AudioCleanerAdapter,
    _bounded_audio_bitrate,
    _exceeds_output_size_limit,
)


def test_bounded_audio_bitrate_keeps_configured_rate_for_short_audio():
    assert _bounded_audio_bitrate("128k", output_duration=600, max_output_size_mb=49) == "128k"


def test_bounded_audio_bitrate_reduces_rate_for_long_audio():
    bitrate = _bounded_audio_bitrate("128k", output_duration=5400, max_output_size_mb=49)
    assert bitrate == "64k"


def test_bounded_audio_bitrate_floors_to_standard_mp3_rate():
    bitrate = _bounded_audio_bitrate("128k", output_duration=4407, max_output_size_mb=49)
    assert bitrate == "80k"


def test_output_size_limit(tmp_path: Path):
    audio = tmp_path / "audio.mp3"
    audio.write_bytes(b"x" * 2048)
    assert _exceeds_output_size_limit(audio, 0.001)
    assert not _exceeds_output_size_limit(audio, 1)


def test_retry_reuses_completed_audio_manifest_without_runtime_dependencies(tmp_path: Path):
    source = tmp_path / "source.mp3"
    source.write_bytes(b"source-audio")
    output_root = tmp_path / "batches"
    batch_dir = output_root / "audio-retry"
    output_dir = batch_dir / "audio_cleaned"
    output_dir.mkdir(parents=True)
    cleaned = output_dir / "source_cleaned.mp3"
    cleaned.write_bytes(b"cleaned-audio")
    result = {
        "batch_id": "audio-retry",
        "local_output_dir": str(output_dir),
        "files": [
            {
                "status": "success",
                "source_path": str(source.resolve()),
                "output_path": str(cleaned.resolve()),
            }
        ],
        "cleaned_files": [
            {
                "status": "success",
                "source_path": str(source.resolve()),
                "output_path": str(cleaned.resolve()),
            }
        ],
        "success_count": 1,
        "failed_count": 0,
    }
    (batch_dir / "manifest.json").write_text(
        json.dumps(result),
        encoding="utf-8",
    )

    adapter = AudioCleanerAdapter(output_root=output_root)
    resumed = adapter.run_batch(
        batch_id="audio-retry",
        files=[source],
        options=AudioCleanOptions(gap_audio_path=str(tmp_path / "missing-gap.mp3")),
        reset_workspace=False,
    )

    assert resumed["resumed_from_checkpoint"] is True
    assert resumed["cleaned_files"][0]["output_path"] == str(cleaned.resolve())
