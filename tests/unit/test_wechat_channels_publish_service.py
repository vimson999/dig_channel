import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.platform_credential_service import (  # noqa: E402
    PLATFORM_DEFINITIONS,
    PURPOSE_DEFINITIONS,
    validate_payload_structure,
)
from services.wechat_channels_publish import (  # noqa: E402
    WechatChannelsAuthenticationRequired,
    WechatChannelsBrowserError,
    _wait_for_classified_page,
    compose_wechat_channels_description,
    playwright_cookies_from_payload,
    publish_wechat_channels_video,
)


class WechatChannelsPublishServiceTest(unittest.TestCase):
    def test_platform_and_purpose_are_exposed(self):
        self.assertIn("wechat_channels", {item["id"] for item in PLATFORM_DEFINITIONS})
        self.assertIn("wechat_channels.publish", {item["id"] for item in PURPOSE_DEFINITIONS})

    def test_cookie_header_gets_weixin_domain_without_leaking_metadata(self):
        cookies = playwright_cookies_from_payload("Cookie: wxuin=abc; token=def")
        self.assertEqual(["wxuin", "token"], [item["name"] for item in cookies])
        self.assertTrue(all(item["domain"] == ".weixin.qq.com" for item in cookies))

    def test_browser_export_cookie_metadata_is_preserved(self):
        payload = json.dumps([{
            "name": "wxuin", "value": "secret", "domain": ".weixin.qq.com",
            "path": "/", "expirationDate": 1999999999, "sameSite": "no_restriction",
            "storeId": "0",
        }])
        cookie = playwright_cookies_from_payload(payload)[0]
        self.assertEqual("None", cookie["sameSite"])
        self.assertEqual(1999999999.0, cookie["expires"])
        self.assertNotIn("storeId", cookie)

    def test_structure_validation_requires_cookie_pairs(self):
        ok, _ = validate_payload_structure("wechat_channels", "cookie_json", "{}")
        self.assertFalse(ok)
        ok, message = validate_payload_structure("wechat_channels", "cookie_json", '{"wxuin":"x"}')
        self.assertTrue(ok)
        self.assertIn("登录校验", message)

    def test_copy_normalizes_topics(self):
        text = compose_wechat_channels_description(" 研报  解读 ", "核心结论", ["#财经", "财经", "AI"])
        self.assertEqual("研报 解读\n核心结论\n#财经 #AI", text)

    def test_spa_classification_waits(self):
        page = Mock()
        with patch("services.wechat_channels_publish._classify_page", side_effect=[
            {"status": "unknown", "message": "loading"}, {"status": "valid", "message": "ready"},
        ]):
            state = _wait_for_classified_page(page, 5000)
        self.assertEqual("valid", state["status"])
        page.wait_for_timeout.assert_called_once_with(500)

    def test_rejects_empty_cookie_and_unknown_visibility_before_browser(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "test.mp4"
            path.write_bytes(b"video")
            with self.assertRaises(WechatChannelsAuthenticationRequired):
                publish_wechat_channels_video(cookies=[], video_path=str(path), title="这是测试标题")
            with self.assertRaisesRegex(WechatChannelsBrowserError, "仅支持公开或仅自己可见"):
                publish_wechat_channels_video(
                    cookies=[{"name": "wxuin", "value": "x"}], video_path=str(path),
                    title="这是测试标题", visibility="friends",
                )

    def test_short_title_is_rejected_before_browser(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "test.mp4"
            path.write_bytes(b"video")
            with self.assertRaisesRegex(WechatChannelsBrowserError, "至少需要 6"):
                publish_wechat_channels_video(
                    cookies=[{"name": "wxuin", "value": "x"}], video_path=str(path), title="短标题",
                )


if __name__ == "__main__":
    unittest.main()
