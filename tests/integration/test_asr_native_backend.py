"""Opt-in native regression for the macOS PyTorch/CTranslate2 OpenMP crash."""
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.skipif(os.environ.get('RUN_NATIVE_ASR_TEST') != '1',
                    reason='Requires the locally cached Whisper medium model')
def test_native_whisper_model_loads_in_fresh_process():
    root = Path(__file__).resolve().parents[2]
    env = {**os.environ, 'PYTHONPATH': str(root / 'src/backend'), 'HF_HUB_OFFLINE': '1'}
    result = subprocess.run(
        [sys.executable, '-u', '-c',
         "from services.asr_service import ASRService\n"
         "service = ASRService('medium')\n"
         "service.use_mlx = False\n"
         "service._ensure_model_loaded()\n"
         "assert service.current_loaded_size == 'medium'\n"
         "print('MODEL_LOADED')\n"],
        cwd=root, env=env, capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'MODEL_LOADED' in result.stdout
