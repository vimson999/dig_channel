import sys
import unittest
from pathlib import Path
from unittest.mock import patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services import zsxq_system_sync_test as system_sync_test  # noqa: E402
from services.zsxq_sync_service import ZsxqTransientUpstreamError  # noqa: E402


class ZsxqSystemSyncTest(unittest.TestCase):
    def test_validate_group_id_treats_1059_as_transient(self):
        with patch.object(
            system_sync_test.zsxq_service,
            "get_menus",
            return_value={
                "succeeded": False,
                "code": "1059",
                "resp_data": {"message": "内部错误"},
            },
        ):
            with self.assertRaises(ZsxqTransientUpstreamError):
                system_sync_test._validate_group_id("28888112822211", cookie="zsxq_access_token=token")

    def test_validate_group_id_with_retry_recovers_after_transient_error(self):
        responses = [
            {"succeeded": False, "code": "1059", "resp_data": {"message": "内部错误"}},
            {"succeeded": False, "code": "1059", "resp_data": {"message": "内部错误"}},
            {"succeeded": True, "resp_data": {"menus": []}},
        ]

        with patch.object(system_sync_test.zsxq_service, "get_menus", side_effect=responses) as get_menus:
            with patch.object(system_sync_test.time, "sleep") as sleep:
                system_sync_test._validate_group_id_with_retry(
                    "28888112822211",
                    cookie="zsxq_access_token=token",
                    max_retries=5,
                    delay_seconds=0,
                )

        self.assertEqual(get_menus.call_count, 3)
        self.assertEqual(sleep.call_count, 0)

    def test_validate_group_id_with_retry_exhausts_retries(self):
        with patch.object(
            system_sync_test.zsxq_service,
            "get_menus",
            return_value={
                "succeeded": False,
                "code": "1059",
                "resp_data": {"message": "内部错误"},
            },
        ) as get_menus:
            with patch.object(system_sync_test.time, "sleep") as sleep:
                with self.assertRaises(ZsxqTransientUpstreamError):
                    system_sync_test._validate_group_id_with_retry(
                        "28888112822211",
                        cookie="zsxq_access_token=token",
                        max_retries=2,
                        delay_seconds=0,
                    )

        self.assertEqual(get_menus.call_count, 3)
        self.assertEqual(sleep.call_count, 0)


if __name__ == "__main__":
    unittest.main()
