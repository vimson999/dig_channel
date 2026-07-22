import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class SharedNavigationAuthGuardTest(unittest.TestCase):
    def test_local_network_failure_keeps_page_open_and_shows_offline_notice(self):
        source = (PROJECT_ROOT / "src" / "frontend" / "js" / "shared-navigation.js").read_text(
            encoding="utf-8"
        )

        catch_block = source.split("}).catch(error => {", 1)[1].split("\n    });", 1)[0]

        self.assertIn("if (isLocal)", catch_block)
        self.assertIn("showLocalBackendOfflineNotice()", catch_block)
        self.assertIn("return true", catch_block)
        self.assertIn("return redirectToLogin('verification_failed')", catch_block)
        self.assertIn("local-backend-offline-notice", source)
        self.assertIn("本地后端暂时无法连接", source)

    def test_invalid_auth_response_still_redirects_to_login(self):
        source = (PROJECT_ROOT / "src" / "frontend" / "js" / "shared-navigation.js").read_text(
            encoding="utf-8"
        )
        response_block = source.split("}).then(async response => {", 1)[1].split("}).catch(error => {", 1)[0]

        self.assertIn("redirectToLogin('session_expired')", response_block)


if __name__ == "__main__":
    unittest.main()
