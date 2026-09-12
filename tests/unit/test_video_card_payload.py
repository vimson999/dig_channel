import json
import sys
import unittest
from datetime import datetime
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BACKEND_DIR = PROJECT_ROOT / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models.channel import Channel  # noqa: E402
from models.video import Video  # noqa: E402


class VideoCardPayloadTest(unittest.TestCase):
    def test_card_payload_keeps_rendered_fields_without_full_description(self):
        channel = Channel(id=4, user_id=1, name="频道名", channel_id="UC1", url="https://example.test")
        video = Video(
            id=8,
            user_id=1,
            video_id="abc",
            channel_id=4,
            channel=channel,
            title="视频标题",
            description="很长的完整描述",
            published_at=datetime(2026, 9, 12, 8, 0, 0),
            duration="PT1M2S",
            view_count=12,
            like_count=3,
            comment_count=1,
            tags=json.dumps(["AI"], ensure_ascii=False),
            thumbnail_url="https://example.test/thumb.jpg",
            category_name="科技",
        )

        payload = video.to_card_dict()

        self.assertEqual(
            {
                "id": 8,
                "video_id": "abc",
                "channel_id": 4,
                "channel_name": "频道名",
                "title": "视频标题",
                "published_at": "2026-09-12T08:00:00",
                "duration": "PT1M2S",
                "view_count": 12,
                "like_count": 3,
                "comment_count": 1,
                "tags": ["AI"],
                "thumbnail_url": "https://example.test/thumb.jpg",
                "category_name": "科技",
                "url": "https://www.youtube.com/watch?v=abc",
            },
            payload,
        )
        self.assertNotIn("description", payload)
        self.assertNotIn("created_at", payload)

    def test_channel_filter_payload_excludes_configuration_fields(self):
        channel = Channel(
            id=4,
            user_id=1,
            name="频道名",
            channel_id="UC1",
            url="https://example.test",
            category="科技",
        )

        self.assertEqual(
            {
                "id": 4,
                "name": "频道名",
                "category": "科技",
                "video_count": 491,
            },
            channel.to_video_filter_dict(video_count=491),
        )


if __name__ == "__main__":
    unittest.main()
