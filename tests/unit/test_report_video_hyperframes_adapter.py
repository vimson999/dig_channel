import unittest

from services.report_video_director import director_package_hash
from services.report_video_hyperframes_adapter import (
    HyperFramesAdapterError,
    authorize_hyperframes_render,
    build_hyperframes_spec,
)


class ReportVideoHyperFramesAdapterTest(unittest.TestCase):
    def _fixtures(self, production_ready=True):
        package = {
            "schema_version": "report_video_director_package_v1",
            "package_id": "director:test",
            "audio_timeline": {
                "audio_seconds": 10,
                "audio_path": "/tmp/real.wav",
                "audio_sha256": "audio-hash",
                "production_ready": production_ready,
            },
            "evidence_enrichment": {"fail_closed": False},
            "segments": [
                {
                    "segment_id": "seg-001",
                    "timeline": {
                        "start_seconds": 0,
                        "duration_seconds": 4,
                    },
                },
                {
                    "segment_id": "seg-002",
                    "timeline": {
                        "start_seconds": 4,
                        "duration_seconds": 6,
                    },
                },
            ],
        }
        package_hash = director_package_hash(package)
        plan = {
            "package_hash": package_hash,
            "dispatch_default": "blocked_until_mode_gate",
            "publish_allowed": False,
            "items": [
                {
                    "segment_id": "seg-001",
                    "media_type": "title_card",
                    "source_pages": [1],
                    "evidence_ids": ["ev-1"],
                    "transition": "fade_through_black",
                    "ratios": {},
                },
                {
                    "segment_id": "seg-002",
                    "media_type": "chart",
                    "source_pages": [3],
                    "evidence_ids": ["ev-2"],
                    "transition": "match_move",
                    "motion_spec": {"camera": "bounded_scale_and_pan"},
                    "ratios": {},
                },
            ],
        }
        return package, plan

    def test_production_spec_uses_real_timeline_and_stays_blocked(self):
        package, plan = self._fixtures()
        spec = build_hyperframes_spec(
            package,
            plan,
            execution_mode="production",
        )

        self.assertEqual(spec["duration_seconds"], 10)
        self.assertEqual(len(spec["clips"]), 2)
        self.assertFalse(spec["publish_allowed"])
        self.assertFalse(spec["render_dispatch_allowed"])
        self.assertEqual(spec["clips"][1]["role"], "source_chart_motion")

    def test_production_rejects_estimated_timeline(self):
        package, plan = self._fixtures(production_ready=False)
        with self.assertRaises(HyperFramesAdapterError):
            build_hyperframes_spec(
                package,
                plan,
                execution_mode="production",
            )

    def test_render_authorization_is_bound_to_gate_hash_and_mode(self):
        package, plan = self._fixtures()
        spec = build_hyperframes_spec(
            package,
            plan,
            execution_mode="production",
        )
        allowed = authorize_hyperframes_render(
            spec,
            full_render_gate={
                "allowed": True,
                "package_hash": spec["package_hash"],
                "execution_mode": "production",
            },
        )
        self.assertTrue(allowed["allowed"])
        self.assertFalse(allowed["publish_allowed"])

        stale = authorize_hyperframes_render(
            spec,
            full_render_gate={
                "allowed": True,
                "package_hash": "stale",
                "execution_mode": "production",
            },
        )
        self.assertFalse(stale["allowed"])
        self.assertIn("gate_package_hash_mismatch", stale["blockers"])


if __name__ == "__main__":
    unittest.main()
