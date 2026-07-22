import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class PublishPlatformTabsTest(unittest.TestCase):
    def test_shared_tabs_are_accessible_and_keep_all_three_platform_panels(self):
        source = (PROJECT_ROOT / "src/frontend/js/platform-browser-publish-panel.js").read_text(encoding="utf-8")

        self.assertIn("window.renderPlatformPublishTabs", source)
        self.assertIn("window.PlatformPublishTabs", source)
        self.assertIn('role="tablist"', source)
        self.assertIn('role="tab"', source)
        self.assertIn('role="tabpanel"', source)
        self.assertIn("ArrowLeft", source)
        self.assertIn("ArrowRight", source)
        for key in ("bilibili", "douyin", "wechat_channels"):
            self.assertIn(key, source)

    def test_both_detail_pages_use_the_shared_publish_center(self):
        mp3 = (PROJECT_ROOT / "src/frontend/js/mp3-video-workflow-detail.js").read_text(encoding="utf-8")
        report = (PROJECT_ROOT / "src/frontend/js/report-audio-workflow-detail.js").read_text(encoding="utf-8")

        self.assertIn("this.renderPublishCenter()", mp3)
        self.assertIn("this.mountPublishTabs()", mp3)
        self.assertIn("prefix: 'mp3'", mp3)
        self.assertIn("this.renderPublishCenter()", report)
        self.assertIn("this.mountPublishTabs()", report)
        self.assertIn("prefix: 'report'", report)

    def test_mp3_detail_exposes_sticky_quick_navigation(self):
        html = (PROJECT_ROOT / "src/frontend/mp3-video-workflow-detail.html").read_text(encoding="utf-8")
        frontend = (PROJECT_ROOT / "src/frontend/js/mp3-video-workflow-detail.js").read_text(encoding="utf-8")

        self.assertIn('id="quick-nav"', html)
        self.assertIn("lg:sticky lg:top-24", html)
        for section_id in (
            "task-overview",
            "content-extract",
            "downloads",
            "visual-assets",
            "video-files",
            "publish-center",
            "run-logs",
        ):
            self.assertIn(f'data-quick-nav="{section_id}"', html)
            self.assertIn(f"this.navSection('{section_id}'", frontend)
        self.assertIn("scrollIntoView({ behavior: 'smooth'", frontend)
        self.assertIn("new IntersectionObserver", frontend)
        self.assertIn("setupQuickNavigation", frontend)


if __name__ == "__main__":
    unittest.main()
