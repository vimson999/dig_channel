import unittest
from datetime import datetime

from scripts.maintenance.zsxq_historical_backfill import classify_topic_window, parse_topic_time


class ZsxqHistoricalBackfillTest(unittest.TestCase):
    def setUp(self):
        self.start = parse_topic_time("2024-01-01T00:00:00+08:00")
        self.end = parse_topic_time("2024-08-11T21:45:37.743+08:00")

    def test_parses_zsxq_timezone_without_colon(self):
        parsed = parse_topic_time("2024-08-11T21:45:37.743+0800")
        self.assertEqual(parsed.utcoffset().total_seconds(), 8 * 60 * 60)

    def test_classifies_target_window_boundaries(self):
        self.assertEqual(classify_topic_window("2024-08-12T00:00:00+0800", self.start, self.end), "newer")
        self.assertEqual(classify_topic_window("2024-06-01T00:00:00+0800", self.start, self.end), "target")
        self.assertEqual(classify_topic_window("2023-12-31T23:59:59+0800", self.start, self.end), "older")
        self.assertEqual(classify_topic_window("not-a-time", self.start, self.end), "invalid")


if __name__ == "__main__":
    unittest.main()
