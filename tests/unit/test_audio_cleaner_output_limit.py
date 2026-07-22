from pathlib import Path

from services.content_pipeline.audio_cleaner_adapter import (
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
