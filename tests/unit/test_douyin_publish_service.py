import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.douyin_publish import (  # noqa: E402
    DouyinAuthenticationRequired,
    DouyinBrowserError,
    _browser_launch_kwargs,
    _classify_page,
    _douyin_profile_dir,
    _publish_page_error,
    _published_title_is_visible,
    _video_upload_state,
    _wait_for_classified_page,
    compose_douyin_description,
    douyin_publish_execution_guard,
    playwright_cookies_from_payload,
    publish_douyin_video,
)
from services.platform_credential_service import validate_payload_structure  # noqa: E402


class DouyinCookieParsingTest(unittest.TestCase):
    def test_credential_has_stable_isolated_browser_profile(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            "os.environ", {"DOUYIN_BROWSER_PROFILE_ROOT": temp_dir}
        ):
            first = _douyin_profile_dir("credential-1")
            second = _douyin_profile_dir("credential-1")
            other = _douyin_profile_dir("credential-2")

        self.assertEqual(first, second)
        self.assertNotEqual(first, other)
        self.assertEqual(Path(temp_dir), first.parent)

    def test_account_guard_records_publish_cooldown_per_credential(self):
        with tempfile.TemporaryDirectory() as temp_dir, patch.dict(
            "os.environ",
            {
                "DOUYIN_PUBLISH_LOCK_ROOT": temp_dir,
                "DOUYIN_PUBLISH_COOLDOWN_SECONDS": "0",
            },
        ):
            with douyin_publish_execution_guard(
                credential_id="credential-1",
                command={"submit": True},
            ):
                pass
            state_files = list(Path(temp_dir).glob("*.lock"))
            state = json.loads(state_files[0].read_text())

        self.assertEqual(1, len(state_files))
        self.assertGreater(state["last_submit_attempt_epoch"], 0)
        self.assertIn("last_released_at", state)

    def test_hidden_login_copy_does_not_interrupt_active_upload(self):
        page = Mock()
        page.url = "https://creator.douyin.com/creator-micro/content/upload"
        page.locator.return_value.inner_text.return_value = (
            "基础信息\n作品上传中，请勿关闭页面\n60%\n手机号登录\n扫码登录"
        )
        page.locator.return_value.count.return_value = 1
        page.get_by_text.return_value.count.return_value = 1
        page.get_by_text.return_value.nth.return_value.is_visible.return_value = False

        state = _classify_page(page)

        self.assertEqual("valid", state["status"])

    def test_visible_login_dialog_still_requires_fresh_cookie(self):
        page = Mock()
        page.url = "https://creator.douyin.com/creator-micro/content/upload"
        page.locator.return_value.inner_text.return_value = "手机号登录"
        page.locator.return_value.count.return_value = 0
        dialog = Mock()
        dialog.count.return_value = 1
        dialog.nth.return_value.is_visible.return_value = True
        dialog.nth.return_value.inner_text.return_value = "扫码登录 手机号登录"
        page.locator.side_effect = lambda selector: (
            dialog if selector == '[role="dialog"]' else Mock(count=Mock(return_value=0))
        )

        state = _classify_page(page)

        self.assertEqual("auth_required", state["status"])

    def test_live_classification_waits_for_creator_spa(self):
        page = Mock()
        with patch(
            "services.douyin_publish._classify_page",
            side_effect=[
                {"status": "unknown", "message": "loading"},
                {"status": "valid", "message": "ready"},
            ],
        ):
            state = _wait_for_classified_page(page, 5000)

        self.assertEqual("valid", state["status"])
        page.wait_for_timeout.assert_called_once_with(500)

    def test_browser_prefers_explicit_executable_and_modern_headless(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            chrome = Path(temp_dir) / "chrome"
            chrome.write_bytes(b"binary")
            with patch.dict(
                "os.environ",
                {"DOUYIN_BROWSER_EXECUTABLE_PATH": str(chrome), "DOUYIN_BROWSER_HEADLESS": "1"},
            ):
                kwargs = _browser_launch_kwargs()

        self.assertEqual(str(chrome), kwargs["executable_path"])
        self.assertFalse(kwargs["headless"])
        self.assertIn("--headless=new", kwargs["args"])

    def test_invalid_explicit_browser_path_has_actionable_error(self):
        with patch.dict(
            "os.environ",
            {"DOUYIN_BROWSER_EXECUTABLE_PATH": "/definitely/missing/chrome"},
        ):
            with self.assertRaisesRegex(DouyinBrowserError, "浏览器路径不存在"):
                _browser_launch_kwargs()

    def test_bundled_browser_also_uses_modern_headless(self):
        with patch.dict("os.environ", {"DOUYIN_BROWSER_HEADLESS": "1"}, clear=True):
            kwargs = _browser_launch_kwargs()

        self.assertNotIn("executable_path", kwargs)
        self.assertFalse(kwargs["headless"])
        self.assertIn("--headless=new", kwargs["args"])

    def test_preserves_browser_export_cookie_metadata(self):
        payload = json.dumps([
            {
                "name": "sessionid_ss",
                "value": "secret",
                "domain": ".douyin.com",
                "path": "/",
                "expirationDate": 1999999999,
                "secure": True,
                "httpOnly": True,
                "sameSite": "no_restriction",
                "storeId": "0",
            }
        ])

        cookies = playwright_cookies_from_payload(payload)

        self.assertEqual(1, len(cookies))
        self.assertEqual("sessionid_ss", cookies[0]["name"])
        self.assertEqual(".douyin.com", cookies[0]["domain"])
        self.assertEqual("None", cookies[0]["sameSite"])
        self.assertEqual(1999999999.0, cookies[0]["expires"])
        self.assertNotIn("storeId", cookies[0])

    def test_cookie_header_gets_safe_douyin_defaults(self):
        cookies = playwright_cookies_from_payload("Cookie: sessionid=abc; sid_guard=def")

        self.assertEqual(["sessionid", "sid_guard"], [item["name"] for item in cookies])
        self.assertTrue(all(item["domain"] == ".douyin.com" for item in cookies))
        self.assertTrue(all(item["path"] == "/" for item in cookies))

    def test_nested_cookie_map_is_supported(self):
        cookies = playwright_cookies_from_payload('{"cookies":{"sessionid":"abc"}}')

        self.assertEqual("sessionid", cookies[0]["name"])
        self.assertEqual("abc", cookies[0]["value"])

    def test_structure_validation_requires_login_cookie(self):
        ok, message = validate_payload_structure("douyin", "cookie_json", '{"passport_csrf_token":"x"}')
        self.assertFalse(ok)
        self.assertIn("sessionid", message)

        ok, message = validate_payload_structure("douyin", "cookie_json", '{"sessionid_ss":"x"}')
        self.assertTrue(ok)
        self.assertIn("登录态", message)

    def test_publish_copy_normalizes_and_deduplicates_topics(self):
        text = compose_douyin_description(
            "  研报   解读  ",
            "核心结论",
            ["#财经", "财经", " 人工智能 "],
        )

        self.assertEqual("研报 解读\n核心结论\n#财经 #人工智能", text)

    def test_manage_card_title_confirms_real_publish(self):
        self.assertTrue(_published_title_is_visible(
            "作品管理 审核中 内资闭门会｜特斯拉机器人量产节奏未变 2026-07-21",
            "内资闭门会 | 特斯拉机器人量产节奏未变 | 供应链筛选标准与龙头公司",
        ))
        self.assertFalse(_published_title_is_visible(
            "作品管理 test test 2026-07-20",
            "内资闭门会 | 特斯拉机器人量产节奏未变 | 供应链筛选标准与龙头公司",
        ))
        self.assertTrue(_published_title_is_visible("全部作品 zz zzz 2026-07-21", "zz"))

    def test_publish_page_error_extracts_platform_rejection(self):
        self.assertEqual("发布失败：请完善作品信息", _publish_page_error("作品发布\n发布失败：请完善作品信息"))
        self.assertIsNone(_publish_page_error("作品发布成功，正在处理"))

    def test_large_file_upload_progress_keeps_page_open(self):
        state = _video_upload_state(
            "作品上传中，请勿关闭页面\n上传完成后将自动发布\n36%\n已上传：316MB/878MB\n剩余时间：2分"
        )
        self.assertTrue(state["in_progress"])
        self.assertFalse(state["completed"])
        self.assertEqual(36, state["progress"])

    def test_upload_completion_is_not_confused_by_auto_publish_copy(self):
        state = _video_upload_state("上传过程中请不要删除/移动文件\n10%\n取消上传")
        self.assertTrue(state["in_progress"])
        self.assertFalse(state["completed"])

        completed = _video_upload_state("视频上传成功\n发布")
        self.assertFalse(completed["in_progress"])
        self.assertTrue(completed["completed"])

    def test_upload_rejects_unsupported_file_before_browser_launch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "unsafe.txt"
            path.write_text("not a video")
            with self.assertRaisesRegex(DouyinBrowserError, "仅支持"):
                publish_douyin_video(cookies=[{"name": "sessionid", "value": "x"}], video_path=str(path), title="标题")

    def test_upload_rejects_empty_cookie_before_browser_launch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "test.mp4"
            path.write_bytes(b"video")
            with self.assertRaises(DouyinAuthenticationRequired):
                publish_douyin_video(cookies=[], video_path=str(path), title="标题")

    def test_upload_rejects_unknown_visibility_before_browser_launch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "test.mp4"
            path.write_bytes(b"video")
            with self.assertRaisesRegex(DouyinBrowserError, "可见范围"):
                publish_douyin_video(
                    cookies=[{"name": "sessionid", "value": "x"}],
                    video_path=str(path),
                    title="标题",
                    visibility="unknown",
                )

    def test_upload_rejects_unknown_declaration_before_browser_launch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "test.mp4"
            path.write_bytes(b"video")
            with self.assertRaisesRegex(DouyinBrowserError, "自主声明"):
                publish_douyin_video(
                    cookies=[{"name": "sessionid", "value": "x"}],
                    video_path=str(path),
                    title="标题",
                    declaration="unknown",
                )


if __name__ == "__main__":
    unittest.main()
