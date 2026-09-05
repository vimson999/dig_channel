import ast
import unittest
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "src/backend/migrations/versions/20260902_zsxq_analysis_history.py"
)


class ZsxqAnalysisMigrationContractTest(unittest.TestCase):
    def test_revision_is_attached_to_current_head_and_creates_all_projections(
        self,
    ):
        source = MIGRATION_PATH.read_text()
        tree = ast.parse(source)
        assignments = {
            node.targets[0].id: ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in {"revision", "down_revision"}
        }
        self.assertEqual(
            assignments["revision"], "20260902_zsxq_analysis_history"
        )
        self.assertEqual(
            assignments["down_revision"],
            "20260829_workflow_notification_outbox",
        )
        for table in (
            "zsxq_analysis_runs",
            "zsxq_analysis_items",
            "zsxq_analysis_topics",
            "zsxq_analysis_keywords",
        ):
            self.assertIn(f'"{table}"', source)
        self.assertNotIn("sa.JSON", source)


if __name__ == "__main__":
    unittest.main()
