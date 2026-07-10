import subprocess
import sys
import unittest
import json
from pathlib import Path
from unittest.mock import Mock, patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.frontend_thumbnail_renderer import (  # noqa: E402
    FrontendThumbnailRenderError,
    FrontendThumbnailRenderer,
)
from services import frontend_thumbnail_render_worker  # noqa: E402


class FrontendThumbnailRendererProcessTest(unittest.TestCase):
    def test_workbench_render_uses_isolated_process(self):
        process = Mock()
        process.returncode = 0
        process.communicate.return_value = ("", "")
        renderer = FrontendThumbnailRenderer()

        with patch("services.frontend_thumbnail_renderer.subprocess.Popen", return_value=process), \
             patch.object(Path, "is_file", return_value=True):
            result = renderer.render_workbench_thumbnail(
                output_path="/tmp/cover.jpg",
                main_title="title",
            )

        self.assertEqual(result, str(Path("/tmp/cover.jpg").resolve()))
        process.communicate.assert_called_once()
        self.assertTrue(process.communicate.call_args.kwargs["timeout"] > 0)

    def test_workbench_render_kills_process_group_on_timeout(self):
        process = Mock()
        process.pid = 12345
        process.communicate.side_effect = [
            subprocess.TimeoutExpired(cmd="renderer", timeout=1),
            ("", "stuck"),
        ]
        renderer = FrontendThumbnailRenderer()

        with patch("services.frontend_thumbnail_renderer.subprocess.Popen", return_value=process), \
             patch.object(renderer, "_terminate_render_process") as terminate, \
             patch.dict("os.environ", {"REPORT_COVER_HARD_TIMEOUT_MS": "1000"}, clear=False):
            with self.assertRaisesRegex(FrontendThumbnailRenderError, "硬超时"):
                renderer.render_workbench_thumbnail(
                    output_path="/tmp/cover.jpg",
                    main_title="title",
                )

        terminate.assert_called_once_with(process)

    def test_worker_restores_expected_size_tuple_after_json_transport(self):
        renderer = Mock()
        payload = {
            "project_root": "/tmp/project",
            "render_kwargs": {
                "output_path": "/tmp/cover.jpg",
                "main_title": "title",
                "expected_size": [1400, 1400],
            },
        }

        with patch.object(sys, "stdin", Mock(read=lambda: json.dumps(payload))), \
             patch.object(frontend_thumbnail_render_worker, "FrontendThumbnailRenderer", return_value=renderer):
            result = frontend_thumbnail_render_worker.main()

        self.assertEqual(result, 0)
        renderer._render_workbench_thumbnail_direct.assert_called_once_with(
            output_path="/tmp/cover.jpg",
            main_title="title",
            expected_size=(1400, 1400),
        )


if __name__ == "__main__":
    unittest.main()
