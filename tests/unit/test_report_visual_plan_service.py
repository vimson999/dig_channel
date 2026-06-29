import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.report_visual_plan_service import (  # noqa: E402
    SCHEMA_VERSION,
    build_visual_plan_preview,
    normalize_visual_plan,
)


class ReportVisualPlanServiceTest(unittest.TestCase):
    def sample_material(self):
        script = (
            "摩根大通认为，AI算力基础设施正在成为新一轮资本开支主线。"
            "第一，数据中心扩张带来电力、网络和散热需求。"
            "第二，政策与产业资本共同推动基础设施投资。"
            "第三，长期看算力底座会影响大模型训练和推理成本。"
        )
        return {
            "title": "摩根大通-第一性原理：AI算力基础设施",
            "pdf": {
                "filename": "摩根大通-第一性原理：AI算力基础设施-260626.pdf",
                "topic_title": "摩根大通：AI算力基础设施",
                "tab_label": "外资研报",
            },
            "structured_result": {"final_script_for_tts": script},
            "final_script_for_tts": script,
        }

    def test_normalize_visual_plan_fills_required_fields(self):
        material = self.sample_material()
        plan = normalize_visual_plan({}, material)

        self.assertIn("opening_cover", plan)
        self.assertIn("logic_cards", plan)
        self.assertEqual(plan["schema_version"], SCHEMA_VERSION)
        self.assertTrue(plan["opening_cover"]["source_title"])
        self.assertTrue(plan["opening_cover"]["hook_title"])
        self.assertTrue(plan["opening_cover"]["cover_title"])
        self.assertGreaterEqual(len(plan["logic_cards"]), 1)
        self.assertEqual(len(plan["logic_cards"][0]["steps"]), 3)
        self.assertGreaterEqual(len(plan["logic_cards"][0]["flow_nodes"]), 4)

    def test_build_visual_plan_preview_renders_images(self):
        material = self.sample_material()
        with tempfile.TemporaryDirectory() as tmpdir:
            result = build_visual_plan_preview(
                material,
                job_id="unit_visual_plan",
                output_root=Path(tmpdir),
            )

            assets = {item["kind"]: item for item in result["preview_assets"]}
            self.assertIn("opening_cover", assets)
            self.assertIn("logic_card", assets)

            for kind in ("opening_cover", "logic_card"):
                path = Path(assets[kind]["path"])
                self.assertTrue(path.exists(), kind)
                with Image.open(path) as rendered:
                    self.assertEqual(rendered.size, (1080, 1920))


if __name__ == "__main__":
    unittest.main()
