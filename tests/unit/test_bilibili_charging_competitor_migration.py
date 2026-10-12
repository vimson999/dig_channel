import ast
import unittest
from pathlib import Path


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "src/backend/migrations/versions/20260930_add_bilibili_charging_competitor.py"
)


class BilibiliChargingCompetitorMigrationTest(unittest.TestCase):
    def test_migration_adds_requested_uid_idempotently_at_current_head(self):
        source = MIGRATION_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        assignments = {
            node.targets[0].id: ast.literal_eval(node.value)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in {"revision", "down_revision"}
        }

        self.assertEqual(assignments["revision"], "20260930_add_bili_charging_competitor")
        self.assertEqual(assignments["down_revision"], "20260912_web_submission_history")
        self.assertIn("3493091757525962", source)
        self.assertIn("ON CONFLICT (uid) DO UPDATE", source)
        self.assertIn("active = true", source)
        self.assertIn("https://space.bilibili.com/3493091757525962/video", source)


if __name__ == "__main__":
    unittest.main()
