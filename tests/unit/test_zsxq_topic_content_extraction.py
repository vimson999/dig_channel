import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.zsxq_sync_service import zsxq_sync_service  # noqa: E402
from models.zsxq_sync import ZsxqTopic  # noqa: E402
from models.task import PublicationTask  # noqa: E402


class ZsxqTopicContentExtractionTest(unittest.TestCase):
    def test_qa_topic_content_includes_question_and_answer(self):
        topic = {
            "type": "q&a",
            "question": {"text": "洪总您好，怎么看铜等工业金属？"},
            "answer": {"text": "感谢提问。\n\n铜最近受到关税影响。"},
        }

        content = zsxq_sync_service._content(topic)

        self.assertIn("提问：洪总您好", content)
        self.assertIn("回答：感谢提问", content)
        self.assertIn("铜最近受到关税影响", content)

    def test_topic_images_include_answer_images(self):
        image = {"image_id": 123, "thumbnail": {"url": "https://example.com/a.jpg"}}
        topic = {"question": {"text": "问题"}, "answer": {"text": "回答", "images": [image]}}

        images = zsxq_sync_service._topic_images(topic)

        self.assertEqual(images, [image])

    def test_topic_model_projects_qa_payload_to_columns(self):
        topic = ZsxqTopic(
            group_id="g1",
            tab_id="t1",
            topic_id="topic-1",
            raw_payload={
                "question": {"text": "怎么看工业金属？"},
                "answer": {"text": "铜和铝都需要关注。", "images": [{"image_id": "img-1"}]},
            },
        )

        self.assertEqual(topic.topic_type, "question_answer")
        self.assertEqual(topic.question_text, "怎么看工业金属？")
        self.assertEqual(topic.answer_text, "铜和铝都需要关注。")
        self.assertIn("提问：怎么看工业金属？", topic.content)
        self.assertIn("回答：铜和铝都需要关注。", topic.content)
        self.assertEqual(topic.images_count, 1)

    def test_public_cached_topic_exposes_raw_images_for_local_list(self):
        image = {
            "image_id": "img-1",
            "thumbnail": {"url": "https://example.com/thumb.jpg", "width": 320, "height": 180},
            "large": {"url": "https://example.com/large.jpg", "width": 1280, "height": 720},
        }
        topic = ZsxqTopic(
            group_id="g1",
            tab_id="t1",
            topic_id="topic-1",
            title="黄金 vs 半导体",
            author_name="洪灏",
            create_time="2026-07-05T13:37:00.000+0800",
            raw_payload={
                "question": {"text": "黄金怎么看？"},
                "answer": {"text": "看图。", "images": [image]},
            },
        )

        payload = zsxq_sync_service._public_cached_topic(topic, [])

        self.assertEqual(payload["answer"]["images"], [image])
        self.assertNotIn("images", payload)


class PublicationTaskProjectionTest(unittest.TestCase):
    def test_waiting_child_id_accepts_existing_aliases(self):
        task = PublicationTask(
            params={"feature": "report_script_material_workflow"},
            result={"waiting_child": {"label": "audio", "id": "child-1", "status": "running"}},
        )

        self.assertEqual(task.waiting_child_label, "audio")
        self.assertEqual(task.waiting_child_id, "child-1")
        self.assertEqual(task.waiting_child_status, "running")


if __name__ == "__main__":
    unittest.main()
