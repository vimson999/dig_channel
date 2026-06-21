import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services import report_video_hash_lookup as hash_lookup  # noqa: E402


class ReportVideoHashLookupTest(unittest.TestCase):
    def test_empty_hashes_do_not_scan_recent_reports(self):
        with patch.object(
            hash_lookup,
            "_lookup_existing_reports_postgres",
            side_effect=AssertionError("empty hashes should not hit postgres lookup"),
        ):
            with patch.object(
                hash_lookup,
                "_lookup_existing_reports_fallback",
                side_effect=AssertionError("empty hashes should not hit fallback lookup"),
            ):
                self.assertEqual(hash_lookup.lookup_existing_reports_by_source_hash([], limit=5000), {})

    def test_hashes_are_deduped_before_lookup(self):
        with patch.object(hash_lookup, "_is_postgres", return_value=True):
            with patch.object(hash_lookup, "_lookup_existing_reports_postgres", return_value={}) as lookup:
                hash_lookup.lookup_existing_reports_by_source_hash(["abc", "", "abc", "def"], limit=10)

        lookup.assert_called_once_with(["abc", "def"], limit=10)


if __name__ == "__main__":
    unittest.main()
