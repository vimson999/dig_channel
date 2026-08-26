import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src" / "backend") not in sys.path:
    sys.path.insert(0, str(ROOT / "src" / "backend"))


class ContentPipelineCanonicalAssetDocsTest(unittest.TestCase):
    def test_operator_docs_describe_migration_backfill_and_raw_retention(self):
        project_context = (ROOT / "docs" / "PROJECT_CONTEXT_MAP.md").read_text(encoding="utf-8")
        readme = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
        notifications = (ROOT / "docs" / "WORKFLOW_EMAIL_NOTIFICATIONS.md").read_text(encoding="utf-8")
        design = (
            ROOT
            / "docs"
            / "superpowers"
            / "specs"
            / "2026-08-25-content-pipeline-canonical-cleaned-assets-design.md"
        ).read_text(encoding="utf-8")
        combined = "\n".join((project_context, readme, notifications, design))

        self.assertIn("alembic upgrade head", combined)
        self.assertIn(
            "backfill_content_pipeline_cleaned_assets.py --dry-run --limit 200",
            combined,
        )
        self.assertIn(
            "backfill_content_pipeline_cleaned_assets.py --limit 200",
            combined,
        )
        self.assertIn("原始下载文件必须保留", combined)
        self.assertIn("清洗产物", combined)
        self.assertIn("计划任务", combined)
        self.assertIn("侧边栏", combined)
        self.assertIn("手动投递", combined)
        self.assertIn("状态：** 已实施", design)


if __name__ == "__main__":
    unittest.main()
