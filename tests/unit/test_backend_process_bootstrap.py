import unittest

from services import backend_process_bootstrap as bootstrap


class BackendProcessBootstrapTest(unittest.TestCase):
    def test_default_schedulers_autostart_core_sync_and_retention(self):
        self.assertEqual(
            bootstrap.DEFAULT_SCHEDULERS,
            ("zsxq_auto", "content_retention"),
        )
        self.assertEqual(
            bootstrap._selected_ids(
                None,
                default=list(bootstrap.DEFAULT_SCHEDULERS),
                allowed=bootstrap.SCHEDULERS.keys(),
            ),
            ["zsxq_auto", "content_retention"],
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


if __name__ == "__main__":
    unittest.main()
