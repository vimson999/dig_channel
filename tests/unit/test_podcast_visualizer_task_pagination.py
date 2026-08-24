import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DOCUMENTS_API = PROJECT_ROOT / "src" / "backend" / "apis" / "v1" / "documents.py"


class PodcastVisualizerTaskPaginationTest(unittest.TestCase):
    def test_history_list_paginates_in_database_without_loading_one_thousand_payloads(self):
        text = DOCUMENTS_API.read_text(encoding="utf-8")
        body = text.split("def list_podcast_visualizer_tasks():", 1)[1].split(
            "\n@bp.route('/podcast-visualizer/requeue/", 1
        )[0]

        self.assertIn("total = base_query.count()", body)
        self.assertIn(".offset((page - 1) * per_page)", body)
        self.assertIn(".limit(per_page)", body)
        self.assertIn("load_only(*PODCAST_TASK_PAYLOAD_COLUMNS)", body)
        self.assertNotIn(".limit(1000)", body)
        self.assertNotIn("page_tasks = tasks[start:end]", body)


if __name__ == "__main__":
    unittest.main()
