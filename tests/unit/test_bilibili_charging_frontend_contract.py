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


class BilibiliChargingFrontendContractTest(unittest.TestCase):
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
