"""音频转文字：文件边界和两种输出的行为测试。"""
import io
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src/backend'))
from services import audio_transcription as service


def test_outputs_share_text_and_round_milliseconds(tmp_path):
    result = service.write_outputs(tmp_path, [
        {'start': 0, 'end': 1.2346, 'text': '你好。'},
        {'start': 3600, 'end': 3601.5, 'text': '世界！'},
        {'start': 3602, 'end': 3603, 'text': '  '},
    ])
    assert (tmp_path / 'transcript.txt').read_text() == '你好。\n世界！\n'
    assert (tmp_path / 'transcript.srt').read_text() == (
        '1\n00:00:00,000 --> 00:00:01,235\n你好。\n\n'
        '2\n01:00:00,000 --> 01:00:01,500\n世界！\n\n')
    assert result['segment_count'] == 2


def test_empty_or_invalid_segments_cannot_complete(tmp_path):
    with pytest.raises(ValueError):
        service.write_outputs(tmp_path, [{'start': float('nan'), 'end': 3, 'text': '坏时间'}])
    assert not (tmp_path / 'transcript.srt').exists()


def test_upload_is_bounded_and_partial_file_removed(tmp_path):
    class BoundedStream(io.BytesIO):
        def read(self, size=-1):
            assert 0 < size <= 1024 * 1024
            return super().read(size)
    path = tmp_path / 'source.mp3'
    with pytest.raises(service.UploadTooLarge):
        service.save_upload(BoundedStream(b'x' * 11), path, limit=10)
    assert not path.exists()
    data = service.save_upload(BoundedStream(b'abc'), path, limit=10)
    assert data['bytes'] == 3
    assert data['sha256'] == service.fingerprint(path)['sha256']


def test_default_limit_prompt_and_paths(monkeypatch, tmp_path):
    monkeypatch.delenv('AUDIO_TRANSCRIPTION_MAX_UPLOAD_MB', raising=False)
    assert service.max_upload_bytes() == 2 * 1024 ** 3
    monkeypatch.setenv('AUDIO_TRANSCRIPTION_MAX_UPLOAD_MB', 'nan')
    assert service.max_upload_bytes() == 2 * 1024 ** 3
    assert '财经' not in service.recognition_prompt('')
    assert '研报' not in service.recognition_prompt('')
    with pytest.raises(ValueError):
        service.safe_file(tmp_path, tmp_path.parent / 'outside')
    with pytest.raises(ValueError):
        service.normalize_filename('evil.exe')


@pytest.mark.skipif(__import__('os').environ.get('RUN_LARGE_UPLOAD_TEST') != '1', reason='按需执行 2 GiB 实际磁盘边界测试')
def test_real_two_gib_stream_boundary(tmp_path):
    class GeneratedStream:
        def __init__(self, size):
            self.remaining = size
        def read(self, size):
            assert 0 < size <= 1024 ** 2
            amount = min(size, self.remaining)
            self.remaining -= amount
            return b'\0' * amount
    limit = 2 * 1024 ** 3
    path = tmp_path / 'large.wav'
    result = service.save_upload(GeneratedStream(limit), path, limit=limit)
    assert result['bytes'] == path.stat().st_size == limit
    path.unlink()
    with pytest.raises(service.UploadTooLarge):
        service.save_upload(GeneratedStream(limit + 1), path, limit=limit)
    assert not path.exists()
