import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


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

    def test_public_cached_topic_exposes_comments(self):
        comment = {
            "comment_id": "comment-1",
            "text": "感谢分享",
            "owner": {"name": "读者"},
            "create_time": "2026-07-16T10:00:00.000+0800",
        }
        topic = ZsxqTopic(
            group_id="g1",
            tab_id="t1",
            topic_id="topic-1",
            raw_payload={"comments_count": 1, "show_comments": [comment]},
        )

        payload = zsxq_sync_service._public_cached_topic(topic, [])

        self.assertEqual(payload["comments_count"], 1)
        self.assertEqual(payload["show_comments"], [comment])

    @patch("services.zsxq_sync_service.zsxq_service.get_comment")
    @patch("services.zsxq_sync_service.zsxq_service.get_topic_comments")
    def test_truncated_comments_are_completed_with_replies(self, get_topic_comments, get_comment):
        get_topic_comments.return_value = {
            "resp_data": {
                "comments": [{"comment_id": "parent", "text": "主评论", "replies_count": 1}],
                "index": "",
            }
        }
        get_comment.return_value = {
            "resp_data": {
                "comment": {
                    "comment_id": "parent",
                    "replied_comments": [{"comment_id": "reply", "text": "回复"}],
                }
            }
        }
        topic = {"topic_id": "topic-1", "comments_count": 2, "show_comments": []}

        zsxq_sync_service._hydrate_truncated_topic_comments("cookie", topic)

        self.assertEqual([item["comment_id"] for item in topic["show_comments"]], ["parent", "reply"])
        self.assertEqual(topic["show_comments"][1]["parent_comment_id"], "parent")
        self.assertFalse(topic["comments_truncated"])

    def test_public_cached_topic_exposes_projected_attachment_images_without_topic_raw_payload(self):
        image = {
            "image_id": "img-1",
            "thumbnail": {"url": "https://example.com/thumb.jpg", "width": 320, "height": 180},
            "large": {"url": "https://example.com/large.jpg", "width": 1280, "height": 720},
        }
        topic = SimpleNamespace(
            topic_id="topic-1",
            topic_uid="",
            title="黄金 vs 半导体",
            create_time="2026-07-05T13:37:00.000+0800",
            raw_json_path=None,
            tab_id="t1",
            tab_label="调研纪要",
            topic_type="question_answer",
            author_name="洪灏",
            content="提问：黄金怎么看？\n\n回答：看图。",
            question_text="黄金怎么看？",
            answer_text="看图。",
            article_title=None,
            article_excerpt=None,
        )
        attachment = SimpleNamespace(
            raw_payload=image,
            name="图片",
            file_id="img-1",
            download_count=None,
            attachment_key="img-1",
            attachment_type="image",
        )

        payload = zsxq_sync_service._public_cached_topic(topic, [attachment])

        self.assertEqual(len(payload["answer"]["images"]), 1)
        self.assertEqual(payload["answer"]["images"][0]["image_id"], "img-1")
        self.assertEqual(payload["answer"]["images"][0]["thumbnail"]["url"], "https://example.com/thumb.jpg")
        self.assertEqual(payload["answer"]["images"][0]["large"]["url"], "https://example.com/large.jpg")
        self.assertNotIn("images", payload)

    def test_projected_attachment_images_keep_answer_and_feed_context(self):
        answer_image = {
            "image_id": "answer-img",
            "_zsxq_image_context": "answer",
            "thumbnail": {"url": "https://example.com/answer-thumb.jpg"},
        }
        feed_image = {
            "image_id": "feed-img",
            "_zsxq_image_context": "talk",
            "thumbnail": {"url": "https://example.com/feed-thumb.jpg"},
        }
        topic = SimpleNamespace(
            topic_id="topic-1",
            topic_uid="",
            title="黄金 vs 半导体",
            create_time="2026-07-05T13:37:00.000+0800",
            raw_json_path=None,
            tab_id="t1",
            tab_label="调研纪要",
            topic_type="question_answer",
            author_name="洪灏",
            content="提问：黄金怎么看？\n\n回答：看图。",
            question_text="黄金怎么看？",
            answer_text="看图。",
            article_title=None,
            article_excerpt=None,
        )
        attachments = [
            SimpleNamespace(
                raw_payload=answer_image,
                name="回答图",
                file_id="answer-img",
                download_count=None,
                attachment_key="answer-img",
                attachment_type="image",
            ),
            SimpleNamespace(
                raw_payload=feed_image,
                name="正文图",
                file_id="feed-img",
                download_count=None,
                attachment_key="feed-img",
                attachment_type="image",
            ),
        ]

        payload = zsxq_sync_service._public_cached_topic(topic, attachments)

        self.assertEqual(payload["answer"]["images"][0]["image_id"], "answer-img")
        self.assertNotIn("_zsxq_image_context", payload["answer"]["images"][0])
        self.assertEqual(payload["images"][0]["image_id"], "feed-img")
        self.assertNotIn("_zsxq_image_context", payload["images"][0])

    def test_topic_images_project_source_context_for_ingest_projection(self):
        topic = {
            "talk": {
                "images": [{"image_id": "talk-img"}],
                "article": {"images": [{"image_id": "article-img"}]},
            },
            "answer": {"images": [{"image_id": "answer-img"}]},
        }

        items = zsxq_sync_service._topic_images_with_context(topic)

        self.assertEqual(
            [(context, payload["image_id"], payload["_zsxq_image_context"]) for context, payload in items],
            [
                ("talk", "talk-img", "talk"),
                ("talk_article", "article-img", "talk_article"),
                ("answer", "answer-img", "answer"),
            ],
        )


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
