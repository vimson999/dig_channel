import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class PublishPlatformTabsTest(unittest.TestCase):
    def test_mp3_detail_lazily_mounts_publish_panels_and_reuses_credentials(self):
        contract = PROJECT_ROOT / "tests/frontend/mp3_publish_lazy_mount_contract.js"
        completed = subprocess.run(
            ["node", str(contract)],
            cwd=PROJECT_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)

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
            "keyframes",
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

    def test_mp3_detail_shows_stage_progress_sidebar_refresh_and_vertical_keyframes(self):
        html = (PROJECT_ROOT / "src/frontend/mp3-video-workflow-detail.html").read_text(encoding="utf-8")
        frontend = (PROJECT_ROOT / "src/frontend/js/mp3-video-workflow-detail.js").read_text(encoding="utf-8")

        self.assertIn('id="sidebar-task-status"', html)
        self.assertIn('id="sidebar-refresh-btn"', html)
        self.assertIn("this.mp3WorkflowStages(job)", frontend)
        self.assertIn("this.renderWorkflowStage(stage)", frontend)
        self.assertIn("this.renderSidebarStatus(job)", frontend)
        self.assertIn("this.renderKeyframes(job)", frontend)
        self.assertIn("横屏和竖屏关键帧均在此展示", frontend)
        self.assertIn("orientation: vertical ? '竖屏' : '横屏'", frontend)

    def test_mp3_bilibili_account_selection_applies_matching_media_suite(self):
        html = (PROJECT_ROOT / "src/frontend/mp3-video-workflow-detail.html").read_text(encoding="utf-8")
        frontend = (PROJECT_ROOT / "src/frontend/js/mp3-video-workflow-detail.js").read_text(encoding="utf-8")

        self.assertIn("accountDefaults: (credential) => this.mp3BilibiliAccountDefaults(credential)", frontend)
        self.assertIn("label.includes('小君君')", frontend)
        self.assertIn("{ videoKey: 'landscape', coverKey: 'xiaojunjun_cover' }", frontend)
        self.assertIn("label.includes('此刻此声')", frontend)
        self.assertIn("{ videoKey: 'vertical', coverKey: 'finance_cover' }", frontend)
        self.assertIn(
            "mp3-video-workflow-detail.js?v=20260826-publish-credential-single-owner-v2",
            html,
        )


if __name__ == "__main__":
    unittest.main()
