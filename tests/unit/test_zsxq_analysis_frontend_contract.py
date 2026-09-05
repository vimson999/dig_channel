import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class ZsxqAnalysisFrontendContractTest(unittest.TestCase):
    def test_page_has_one_entry_two_independent_tabs_and_history_regions(self):
        html = (PROJECT_ROOT / "src/frontend/zsxq-analysis.html").read_text()
        self.assertIn('href="css/zsxq-lab.css', html)
        self.assertIn('data-analysis-kind="pdf"', html)
        self.assertIn('data-analysis-kind="audio"', html)
        self.assertIn('id="analysis-upload-form"', html)
        self.assertIn('id="analysis-history-list"', html)
        self.assertIn('id="analysis-keyword-cloud"', html)
        self.assertIn('id="analysis-trend-chart"', html)

    def test_frontend_uses_separate_history_detail_and_item_endpoints(self):
        script = (
            PROJECT_ROOT / "src/frontend/js/zsxq-analysis.js"
        ).read_text()
        self.assertIn("/zsxq-analysis/runs", script)
        self.assertIn("/items", script)
        self.assertIn("/source", script)
        self.assertIn("state.kind", script)

    def test_audio_metrics_render_short_medium_and_long_duration_buckets(self):
        script = (
            PROJECT_ROOT / "src/frontend/js/zsxq-analysis.js"
        ).read_text(encoding="utf-8")

        self.assertIn("duration_distribution", script)
        self.assertIn("短 / 中 / 长", script)
        self.assertNotIn(
            "Promise.all([loadHistory('pdf'), loadHistory('audio')])", script
        )

    def test_upload_and_detail_responses_are_bound_to_request_tab(self):
        script = (
            PROJECT_ROOT / "src/frontend/js/zsxq-analysis.js"
        ).read_text(encoding="utf-8")

        self.assertIn("const fileAtRequest = state.selectedFile", script)
        self.assertIn("formData.append('kind', kindAtRequest)", script)
        self.assertIn("formData.append('file', fileAtRequest)", script)
        self.assertIn("state.currentRunByKind.set(kindAtRequest", script)
        self.assertIn("detailRequestSerial", script)
        self.assertNotIn("state.currentRunByKind.set(state.kind", script)

    def test_shared_navigation_knows_the_new_page(self):
        script = (
            PROJECT_ROOT / "src/frontend/js/shared-navigation.js"
        ).read_text()
        self.assertIn("filename === 'zsxq-analysis.html'", script)
        self.assertIn("id: 'zsxq-analysis'", script)
        self.assertIn("href: 'zsxq-analysis.html'", script)


if __name__ == "__main__":
    unittest.main()
