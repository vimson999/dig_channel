import unittest
from contextlib import contextmanager
from unittest.mock import patch

from services import backend_process_bootstrap as bootstrap


class BackendProcessBootstrapTest(unittest.TestCase):
    def test_default_schedulers_autostart_core_sync_retention_and_daily_dashboards(self):
        self.assertEqual(
            bootstrap.DEFAULT_SCHEDULERS,
            (
                "zsxq_auto",
                "content_retention",
                "bilibili_charging",
                "youtube_channel_sync",
                "youtube_video_sync",
            ),
        )
        self.assertEqual(
            bootstrap._selected_ids(
                None,
                default=list(bootstrap.DEFAULT_SCHEDULERS),
                allowed=bootstrap.SCHEDULERS.keys(),
            ),
            [
                "zsxq_auto",
                "content_retention",
                "bilibili_charging",
                "youtube_channel_sync",
                "youtube_video_sync",
            ],
        )

    def test_scheduler_autostart_can_be_disabled_explicitly(self):
        for raw in ("none", "off", "false", "0", ""):
            with self.subTest(raw=raw):
                self.assertEqual(
                    bootstrap._selected_ids(
                        raw,
                        default=list(bootstrap.DEFAULT_SCHEDULERS),
                        allowed=bootstrap.SCHEDULERS.keys(),
                    ),
                    [],
                )

    def test_auto_publish_scheduler_stays_manual_by_default(self):
        self.assertNotIn("zsxq_auto_publish", bootstrap.DEFAULT_SCHEDULERS)

    def test_youtube_scheduler_processes_do_not_recursively_run_bootstrap(self):
        for runner in (
            "run_youtube_channel_sync_scheduler.py",
            "run_youtube_video_sync_scheduler.py",
        ):
            with self.subTest(runner=runner), patch.object(
                bootstrap.sys,
                "argv",
                ["python", f"src/backend/{runner}", "start"],
            ):
                self.assertEqual(
                    bootstrap._current_process_skip_reason(),
                    f"process_context:{runner}",
                )

    def test_async_bootstrap_enters_the_flask_application_context(self):
        events = []

        class FakeApp:
            @contextmanager
            def app_context(self):
                events.append("enter")
                try:
                    yield
                finally:
                    events.append("exit")

        with patch.object(
            bootstrap,
            "run_backend_process_bootstrap",
            side_effect=lambda app: events.append(("run", app)),
        ):
            app = FakeApp()
            bootstrap._run_and_log(app)

        self.assertEqual(events, ["enter", ("run", app), "exit"])


if __name__ == "__main__":
    unittest.main()
