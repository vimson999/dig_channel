import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services import script_video_workflow as service  # noqa: E402


class ScriptVideoWorkflowServiceTest(unittest.TestCase):
    def test_initial_scene_rows_cover_the_source_script_in_order(self):
        source = "第一句。第二句！第三句？"

        rows = service.build_initial_scenes(source, max_chars=5)

        self.assertEqual([1, 2, 3], [row["sequence"] for row in rows])
        self.assertEqual(source, "".join(row["script_text"] for row in rows))
        self.assertEqual(
            rows,
            service.validate_scene_rows(rows, source, require_prompts=False),
        )

    def test_scene_validation_rejects_a_gap_before_image_generation(self):
        with self.assertRaisesRegex(ValueError, "未完整覆盖"):
            service.validate_scene_rows(
                [{"sequence": 1, "script_text": "第一句。", "image_prompt": "画面 A"}],
                "第一句。第二句。",
                require_prompts=True,
            )

    def test_image_prompt_includes_user_scene_context_and_scheme_contract(self):
        original = service.scene_image_prompt_for_scheme
        service.scene_image_prompt_for_scheme = (
            lambda prompt, text, *, scheme_key: f"{scheme_key}|{text}|{prompt}"
        )
        try:
            self.assertEqual(
                "stick_figure|市场出现拐点。|折线向上穿越门槛",
                service.compose_scene_image_prompt(
                    {
                        "script_text": "市场出现拐点。",
                        "image_prompt": "折线向上穿越门槛",
                    },
                    "stick_figure",
                ),
            )
        finally:
            service.scene_image_prompt_for_scheme = original

    def test_audio_chunks_use_sentence_first_splitter_with_bounded_size(self):
        chunks = service.split_audio_chunks("第一句。第二句。", max_chars=1)

        self.assertEqual(["第一句。第二句。"], chunks)

    def test_audio_chunk_coverage_ignores_splitter_only_layout_whitespace(self):
        source = "".join(f"第{index}句。" for index in range(1, 180))
        chunks = service.split_audio_chunks(source, max_chars=200)

        self.assertGreater(len(chunks), 1)
        self.assertTrue(service.audio_chunks_cover_source(chunks, source))

    def test_output_paths_are_kept_within_the_workflow_directory(self):
        with TemporaryDirectory() as tempdir:
            root = Path(tempdir) / "workflow"
            root.mkdir()
            allowed = root / "audio" / "merged.wav"
            allowed.parent.mkdir()
            allowed.touch()

            self.assertEqual(allowed.resolve(), service.safe_output_file(root, allowed))
            with self.assertRaisesRegex(ValueError, "工作流目录"):
                service.safe_output_file(root, Path(tempdir) / "outside.wav")

    def test_changed_scene_content_gets_a_distinct_image_artifact_path(self):
        with TemporaryDirectory() as tempdir:
            from unittest.mock import patch

            with patch.dict("os.environ", {"SCRIPT_VIDEO_WORKFLOW_OUTPUT_ROOT": tempdir}, clear=False):
                original = service.scene_image_output_path("workflow-1", 1, artifact_key="a" * 64)
                changed = service.scene_image_output_path("workflow-1", 1, artifact_key="b" * 64)

        self.assertNotEqual(original, changed)
        self.assertEqual(original.parent, changed.parent)


if __name__ == "__main__":
    unittest.main()
