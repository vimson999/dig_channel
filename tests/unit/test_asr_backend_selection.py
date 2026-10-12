"""ASR must select its native backend without requiring PyTorch/OpenMP."""
import importlib.util
from pathlib import Path
import sys
from types import ModuleType

import pytest


SERVICE = Path(__file__).resolve().parents[2] / 'src/backend/services/asr_service.py'


@pytest.mark.parametrize('cuda_devices, device, compute_type', [
    (0, 'cpu', 'int8'),
    (1, 'cuda', 'float16'),
])
def test_faster_whisper_loads_without_torch(monkeypatch, tmp_path, cuda_devices, device, compute_type):
    # Native inference is expensive and GPU availability depends on the host.
    # Keep service import, device selection, and initialization real.
    native = ModuleType('ctranslate2')
    native.get_cuda_device_count = lambda: cuda_devices
    whisper = ModuleType('faster_whisper')

    class Model:
        def __init__(self, size, *, device, compute_type, download_root):
            self.size = size
            self.device = device
            self.compute_type = compute_type

    whisper.WhisperModel = Model
    monkeypatch.setitem(sys.modules, 'torch', None)
    monkeypatch.setitem(sys.modules, 'mlx_whisper', None)
    monkeypatch.setitem(sys.modules, 'ctranslate2', native)
    monkeypatch.setitem(sys.modules, 'faster_whisper', whisper)
    spec = importlib.util.spec_from_file_location('asr_backend_under_test', SERVICE)
    module = importlib.util.module_from_spec(spec)
    # The module's singleton should use a disposable model-cache directory.
    module.__file__ = str(tmp_path / 'src/backend/services/asr_service.py')
    spec.loader.exec_module(module)
    service = module.ASRService('medium')
    service._ensure_model_loaded()

    assert service.model.device == device
    assert service.model.compute_type == compute_type
    assert service.model.size == 'medium'
    assert service.current_loaded_size == 'medium'
