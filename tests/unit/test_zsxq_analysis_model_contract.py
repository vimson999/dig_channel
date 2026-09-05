import sys
import unittest
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import (  # noqa: E402
    ZsxqAnalysisItem,
    ZsxqAnalysisKeyword,
    ZsxqAnalysisRun,
    ZsxqAnalysisTopic,
)


class ZsxqAnalysisModelContractTest(unittest.TestCase):
    def test_analysis_tables_are_structured_and_have_no_json_payload_columns(
        self,
    ):
        models = [
            ZsxqAnalysisRun,
            ZsxqAnalysisItem,
            ZsxqAnalysisTopic,
            ZsxqAnalysisKeyword,
        ]
        expected_tables = {
            "zsxq_analysis_runs",
            "zsxq_analysis_items",
            "zsxq_analysis_topics",
            "zsxq_analysis_keywords",
        }
        self.assertEqual(
            {model.__tablename__ for model in models}, expected_tables
        )

        for model in models:
            self.assertNotIn(
                "JSON",
                {
                    column.type.__class__.__name__
                    for column in model.__table__.columns
                },
            )

        self.assertIn("user_id", ZsxqAnalysisRun.__table__.columns)
        self.assertIn(
            "source_artifact_path", ZsxqAnalysisRun.__table__.columns
        )
        self.assertIn("item_count", ZsxqAnalysisRun.__table__.columns)
        self.assertIn("download_count", ZsxqAnalysisItem.__table__.columns)
        self.assertIn("rank", ZsxqAnalysisKeyword.__table__.columns)


if __name__ == "__main__":
    unittest.main()
