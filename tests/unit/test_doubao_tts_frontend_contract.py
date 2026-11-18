import subprocess
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_doubao_tts_history_polling_is_active_only_and_audio_is_lazy():
    contract = PROJECT_ROOT / "tests/frontend/doubao-tts-polling.test.cjs"
    completed = subprocess.run(
        ["node", str(contract)],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
