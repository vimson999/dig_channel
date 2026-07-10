import importlib.util
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "dev" / "review_bug_records.py"


def _load_review_module():
    spec = importlib.util.spec_from_file_location("review_bug_records", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BugReviewDisciplineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.review = _load_review_module()

    def test_all_bug_records_are_classified_and_review_is_current(self):
        summary, issues = self.review.validate_registry(self.review.load_registry())

        self.assertEqual([], issues)
        self.assertEqual(summary["record_count"], summary["classified_count"])
        self.assertEqual([], summary["unclassified"])

    def test_recurring_clusters_have_systemic_guardrails(self):
        registry = self.review.load_registry()

        for cluster in registry["clusters"]:
            self.assertGreaterEqual(len(set(cluster["records"])), 2, cluster["key"])
            self.assertIn(cluster["priority"], {"P0", "P1", "P2"})
            self.assertTrue(cluster["systemic_guardrail"].strip(), cluster["key"])


if __name__ == "__main__":
    unittest.main()
