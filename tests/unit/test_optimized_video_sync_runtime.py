import sys
import unittest
import os
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from config.base import BaseConfig  # noqa: E402
from services import optimized_video_sync_service as module  # noqa: E402


class OptimizedVideoSyncRuntimeTest(unittest.TestCase):
    def tearDown(self):
        module.VideoSyncCache._unavailable_until = 0.0
        module.OptimizedVideoSyncService._sync_in_progress.clear()

    def test_cache_client_uses_configured_redis_url(self):
        client = MagicMock()
        with patch.object(module.redis.Redis, "from_url", return_value=client) as factory:
            cache = module.VideoSyncCache()

        self.assertIs(cache.redis_client, client)
        factory.assert_called_once_with(
            BaseConfig.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=3,
            socket_timeout=3,
            health_check_interval=30,
        )

    def test_cache_failure_opens_circuit_and_suppresses_repeated_connections(self):
        cache = module.VideoSyncCache()
        cache.redis_client = MagicMock()
        cache.redis_client.ping.side_effect = ConnectionError("unreachable")

        self.assertFalse(cache.is_available(force=True))
        self.assertIsNone(cache.get_sync_status(7))
        cache.redis_client.ping.assert_called_once()
        cache.redis_client.get.assert_not_called()

    def test_daily_snapshot_is_one_db_side_insert_under_advisory_lock(self):
        fake_db = MagicMock()
        fake_db.session.execute.side_effect = [
            SimpleNamespace(rowcount=1),
            SimpleNamespace(rowcount=7),
        ]
        service = object.__new__(module.OptimizedVideoSyncService)

        with patch.object(module, "db", fake_db):
            service._create_video_data_records_batch(42, date(2026, 7, 23))

        self.assertEqual(fake_db.session.execute.call_count, 2)
        lock_sql = str(fake_db.session.execute.call_args_list[0].args[0])
        insert_sql = str(fake_db.session.execute.call_args_list[1].args[0])
        self.assertIn("pg_advisory_xact_lock", lock_sql)
        self.assertIn("INSERT INTO public.video_data", insert_sql)
        self.assertIn("NOT EXISTS", insert_sql)
        fake_db.session.commit.assert_called_once_with()
        fake_db.session.rollback.assert_not_called()

    def test_sync_single_flight_is_shared_across_service_instances(self):
        service_a = object.__new__(module.OptimizedVideoSyncService)
        service_b = object.__new__(module.OptimizedVideoSyncService)

        self.assertTrue(service_a._acquire_sync_slot("all_channels_9"))
        self.assertFalse(service_b._acquire_sync_slot("all_channels_9"))
        service_a._release_sync_slot("all_channels_9")
        self.assertTrue(service_b._acquire_sync_slot("all_channels_9"))

    def test_parallelism_has_a_conservative_process_limit(self):
        with patch.dict(os.environ, {"OPTIMIZED_VIDEO_SYNC_MAX_WORKERS": "3"}):
            self.assertEqual(
                module.OptimizedVideoSyncService._bounded_max_workers(10),
                3,
            )
            self.assertEqual(
                module.OptimizedVideoSyncService._bounded_max_workers(2),
                2,
            )


if __name__ == "__main__":
    unittest.main()
