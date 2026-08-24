import sys
import tempfile
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from services.workflow_worker_readiness import (  # noqa: E402
    REPORT_AUDIO_DEPENDENCY_QUEUES,
    record_worker_readiness,
    report_audio_worker_readiness,
)


class WorkflowWorkerReadinessTest(unittest.TestCase):
    def test_report_audio_readiness_requires_every_dependency_queue_at_current_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            missing = report_audio_worker_readiness(runtime_dir=directory, revision="rev-a")
            self.assertFalse(missing["ready"])
            self.assertEqual(set(missing["missing_queues"]), set(REPORT_AUDIO_DEPENDENCY_QUEUES))

            for queue in REPORT_AUDIO_DEPENDENCY_QUEUES:
                record_worker_readiness(
                    queue,
                    worker_id=f"{queue}-1",
                    revision="rev-a",
                    runtime_dir=directory,
                )

            ready = report_audio_worker_readiness(runtime_dir=directory, revision="rev-a")
            self.assertTrue(ready["ready"])
            self.assertEqual(ready["missing_queues"], [])

            stale = report_audio_worker_readiness(runtime_dir=directory, revision="rev-b")
            self.assertFalse(stale["ready"])
            self.assertEqual(set(stale["stale_queues"]), set(REPORT_AUDIO_DEPENDENCY_QUEUES))

    def test_mixed_worker_revisions_block_submission_even_when_one_worker_is_current(self):
        with tempfile.TemporaryDirectory() as directory:
            for queue in REPORT_AUDIO_DEPENDENCY_QUEUES:
                record_worker_readiness(queue, worker_id=f"{queue}-new", revision="rev-b", runtime_dir=directory)
            record_worker_readiness(
                "doubao_browser_tts",
                worker_id="doubao_browser_tts-old",
                revision="rev-a",
                runtime_dir=directory,
            )

            readiness = report_audio_worker_readiness(runtime_dir=directory, revision="rev-b")

            self.assertFalse(readiness["ready"])
            self.assertEqual(readiness["stale_queues"], ["doubao_browser_tts"])
