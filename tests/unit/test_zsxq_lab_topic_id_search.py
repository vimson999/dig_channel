import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


class ZsxqLabTopicIdSearchContractTest(unittest.TestCase):
    def test_search_ui_names_topic_id_and_frontend_uses_exact_lookup(self):
        html = (ROOT / "src/frontend/zsxq-lab.html").read_text(encoding="utf-8")
        script = (ROOT / "src/frontend/js/zsxq-lab.js").read_text(encoding="utf-8")

        self.assertIn("关键词或帖子 ID", html)
        self.assertIn("完整帖子 ID", html)
        self.assertIn("function localTopicIdQuery()", script)
        self.assertIn("topic_id: topicId", script)
        self.assertIn("requestAnimationFrame(locateInspectorTopic)", script)


if __name__ == "__main__":
    unittest.main()
