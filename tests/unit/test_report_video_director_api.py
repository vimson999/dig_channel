import unittest

from flask import Flask

from api.report_video_director_workflow import (
    report_video_director_workflow_bp,
)


class ReportVideoDirectorApiTest(unittest.TestCase):
    def test_status_exposes_fail_closed_production_policy(self):
        app = Flask(__name__)
        app.register_blueprint(report_video_director_workflow_bp)
        response = app.test_client().get(
            "/api/report-video-director-workflows/status"
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()["data"]
        self.assertEqual(payload["production_human_gate"], "required")
        self.assertFalse(payload["publish_allowed"])
        self.assertFalse(payload["existing_production_task_mutation"])


if __name__ == "__main__":
    unittest.main()
