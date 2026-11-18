import unittest
from html.parser import HTMLParser
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class _ChargingRankingMarkupParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.elements = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if (
            attributes.get("id", "").startswith("bilibili-charging-growth-ranking")
            or "data-bilibili-charging-ranking-period" in attributes
        ):
            self.elements.append((tag, attributes))


class _ChargingSummaryHeaderParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_summary_table = False
        self.in_header = False
        self.headers = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if tag == "table" and attributes.get("aria-label") == "B站充电人数汇总":
            self.in_summary_table = True
        elif self.in_summary_table and tag == "th":
            self.in_header = True
            self.headers.append("")

    def handle_data(self, data):
        if self.in_header:
            self.headers[-1] += data

    def handle_endtag(self, tag):
        if tag == "th" and self.in_header:
            self.in_header = False
        elif tag == "table" and self.in_summary_table:
            self.in_summary_table = False


class BilibiliChargingFrontendContractTest(unittest.TestCase):
    def test_homepage_charging_summary_exposes_renewal_adjusted_income_columns(self):
        parser = _ChargingSummaryHeaderParser()
        parser.feed((PROJECT_ROOT / "src" / "frontend" / "index.html").read_text("utf-8"))

        self.assertEqual(
            [header.strip() for header in parser.headers],
            [
                "UP 主",
                "最新充电人数",
                "快照日净增",
                "净增估算金额（CNY）",
                "净增估算分成（CNY）",
                "续费率",
                "按续费率估算月收入（CNY）",
            ],
        )

    def test_homepage_exposes_accessible_growth_ranking_tabs_and_panel(self):
        parser = _ChargingRankingMarkupParser()
        parser.feed((PROJECT_ROOT / "src" / "frontend" / "index.html").read_text("utf-8"))

        by_id = {
            attributes["id"]: (tag, attributes)
            for tag, attributes in parser.elements
            if attributes.get("id")
        }
        self.assertEqual(by_id["bilibili-charging-growth-ranking-tabs"][1]["role"], "tablist")
        panel = by_id["bilibili-charging-growth-ranking-panel"][1]
        self.assertEqual(panel["role"], "tabpanel")
        self.assertEqual(panel["aria-labelledby"], "bilibili-charging-growth-ranking-tab-day")

        tabs = [
            attributes
            for tag, attributes in parser.elements
            if tag == "button" and "data-bilibili-charging-ranking-period" in attributes
        ]
        self.assertEqual(
            {tab["data-bilibili-charging-ranking-period"] for tab in tabs},
            {"day", "week", "month"},
        )
        self.assertTrue(all(tab.get("role") == "tab" for tab in tabs))
        self.assertEqual(
            {tab["id"] for tab in tabs},
            {
                "bilibili-charging-growth-ranking-tab-day",
                "bilibili-charging-growth-ranking-tab-week",
                "bilibili-charging-growth-ranking-tab-month",
            },
        )
        self.assertEqual([tab["tabindex"] for tab in tabs], ["0", "-1", "-1"])
        self.assertTrue(
            all(tab.get("aria-controls") == "bilibili-charging-growth-ranking-panel" for tab in tabs)
        )


if __name__ == "__main__":
    unittest.main()
