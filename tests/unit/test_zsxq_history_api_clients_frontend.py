import subprocess
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = PROJECT_ROOT / "src" / "frontend"
PAGE_PATH = FRONTEND_ROOT / "zsxq-history-api-clients.html"
SCRIPT_PATH = FRONTEND_ROOT / "js" / "zsxq-history-api-clients.js"
NAV_PATH = FRONTEND_ROOT / "js" / "shared-navigation.js"


class ZsxqHistoryApiClientsFrontendTests(unittest.TestCase):
    def test_page_wires_the_admin_api_management_script(self):
        page = PAGE_PATH.read_text(encoding="utf-8")

        self.assertIn('id="client-form"', page)
        self.assertIn('id="one-time-secret-panel"', page)
        self.assertIn('js/zsxq-history-api-clients.js', page)
        self.assertIn('Secret 仅在创建或轮换后显示一次', page)

    def test_script_uses_only_admin_management_endpoints(self):
        source = SCRIPT_PATH.read_text(encoding="utf-8")

        self.assertIn("/zsxq/history-api-clients?", source)
        self.assertIn("'/zsxq/history-api-clients'", source)
        self.assertIn("/zsxq/history-api-clients/${encodeURIComponent(clientId)}/rotate", source)
        self.assertIn("/zsxq/history-api-clients/${encodeURIComponent(clientId)}/revoke", source)
        self.assertIn("credentials: 'same-origin'", source)
        self.assertIn("scopes: ['zsxq.history.read']", source)

    def test_secret_is_one_time_only_and_not_persisted_or_logged(self):
        source = SCRIPT_PATH.read_text(encoding="utf-8")

        self.assertIn('credentials?.secret', source)
        self.assertIn('function dismissSecret()', source)
        self.assertIn("els.issuedSecret.textContent = ''", source)
        self.assertNotIn("localStorage.setItem", source)
        self.assertNotIn("sessionStorage.setItem", source)
        self.assertNotIn("console.log", source)

    def test_navigation_exposes_the_admin_page(self):
        source = NAV_PATH.read_text(encoding="utf-8")

        self.assertIn('"id": "zsxq-history-api-clients"', source)
        self.assertIn('"name": "星球 API 调用方"', source)
        self.assertIn('"href": "zsxq-history-api-clients.html"', source)

    def test_script_has_valid_javascript_syntax(self):
        result = subprocess.run(
            ["node", "--check", str(SCRIPT_PATH)],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
