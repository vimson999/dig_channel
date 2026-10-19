import io
import subprocess
import tempfile
import wave
from pathlib import Path

import pytest


def test_export_trims_and_changes_duration_without_changing_source():
    from services.audio_clip_export import export_audio_clip
    with tempfile.TemporaryDirectory() as directory:
        source = Path(directory) / 'source.wav'
        with wave.open(str(source), 'wb') as audio:
            audio.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
            audio.writeframes(b'\x00\x00' * 16000 * 6)
        before = source.read_bytes()
        target = Path(directory) / 'clip.mp3'
        export_audio_clip(source, target, start='00:01', end='00:05', speed='2')
        duration = float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'default=nw=1:nk=1', str(target)]))
        assert 1.9 < duration < 2.2
        assert source.read_bytes() == before


@pytest.mark.parametrize('start,end,speed', [('4', '2', '1'), ('-1', '', '1'), ('00:99', '', '1'), ('0', '', 'nan'), ('0', '', '0'), ('0', '', '5')])
def test_invalid_range_or_speed_is_rejected(start, end, speed):
    from services.audio_clip_export import clip_options
    with pytest.raises(ValueError):
        clip_options(start, end, speed)


def test_upload_endpoint_returns_downloadable_mp3_after_temp_cleanup():
    from flask import Flask, g
    from types import SimpleNamespace
    from unittest.mock import patch
    from api.mp3_video_workflow import mp3_video_workflow_bp
    audio = io.BytesIO()
    with wave.open(audio, 'wb') as wav:
        wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\x00\x00' * 16000 * 3)
    audio.seek(0)
    app = Flask(__name__)
    app.register_blueprint(mp3_video_workflow_bp)

    @app.before_request
    def authenticated():
        g.current_user = SimpleNamespace(id='test', is_active=True)

    with patch('api.mp3_video_workflow.release_db_session_quietly'):
        response = app.test_client().post('/api/mp3-video-workflows/audio-export', data={
            'audio': (audio, '中文.wav'), 'start': '0', 'end': '2', 'speed': '2',
        })
    assert response.status_code == 200
    assert response.mimetype == 'audio/mpeg'
    assert 'attachment' in response.headers['Content-Disposition']
    assert len(response.data) > 1000
