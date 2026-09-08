import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class TtsLegacyApiCapabilitiesTest(unittest.TestCase):
    def test_missing_chattts_package_is_not_advertised(self):
        script = textwrap.dedent(
            """
            import builtins
            import importlib.util
            import sys
            import types

            real_import = builtins.__import__
            real_find_spec = importlib.util.find_spec

            def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
                if name == "TTS.api":
                    raise ImportError("simulated missing Coqui")
                return real_import(name, globals, locals, fromlist, level)

            def guarded_find_spec(name, package=None):
                if name == "ChatTTS":
                    return None
                return real_find_spec(name, package)

            builtins.__import__ = guarded_import
            importlib.util.find_spec = guarded_find_spec

            fake_chat_service = types.ModuleType("services.chat_tts_service")
            fake_chat_service.chat_tts_service = object()
            sys.modules["services.chat_tts_service"] = fake_chat_service

            from api import tts

            assert tts.CHAT_TTS_AVAILABLE is False
            assert tts.TTS_AVAILABLE is False
            assert tts.get_available_model_keys() == []
            """
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROJECT_ROOT / "src" / "backend")

        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_chattts_only_install_does_not_advertise_coqui_models_or_load_one_for_status(self):
        script = textwrap.dedent(
            """
            import builtins
            import sys
            import types

            real_import = builtins.__import__

            def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
                if name == "TTS.api":
                    raise OSError("simulated Coqui binary ABI mismatch")
                return real_import(name, globals, locals, fromlist, level)

            builtins.__import__ = guarded_import

            fake_chat_service = types.ModuleType("services.chat_tts_service")
            fake_chat_service.chat_tts_service = object()
            sys.modules["services.chat_tts_service"] = fake_chat_service

            from flask import Flask
            from api import tts

            def unexpected_model_load(*args, **kwargs):
                raise AssertionError("status endpoint must not initialize a model")

            tts.get_tts_instance = unexpected_model_load
            app = Flask(__name__)
            app.register_blueprint(tts.tts_bp)

            with app.test_client() as client:
                models_response = client.get("/api/v1/tts/models")
                assert models_response.status_code == 200
                models_data = models_response.get_json()["data"]
                assert [item["key"] for item in models_data["models"]] == ["chattts_v1"]
                assert models_data["default_model"] == "chattts_v1"

                status_response = client.get("/api/v1/tts/status")
                assert status_response.status_code == 200
                status_data = status_response.get_json()["data"]
                assert status_data["available_models"] == ["chattts_v1"]
                assert status_data["default_model_ready"] is False
            """
        )
        env = os.environ.copy()
        env["PYTHONPATH"] = str(PROJECT_ROOT / "src" / "backend")

        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
