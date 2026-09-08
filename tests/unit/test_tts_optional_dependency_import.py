import os
from pathlib import Path
import subprocess
import sys
import textwrap
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class TtsOptionalDependencyImportTest(unittest.TestCase):
    def test_coqui_binary_load_error_does_not_prevent_api_import(self):
        script = textwrap.dedent(
            """
            import builtins

            real_import = builtins.__import__

            def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
                if name == "TTS.api":
                    raise OSError("simulated binary ABI mismatch")
                return real_import(name, globals, locals, fromlist, level)

            builtins.__import__ = guarded_import

            from api import tts

            assert tts.TTS_COQUI_AVAILABLE is False
            print("api.tts imported with Coqui disabled")
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
        self.assertIn("api.tts imported with Coqui disabled", result.stdout)


if __name__ == "__main__":
    unittest.main()
