import sys
import unittest
from pathlib import Path

from flask import Flask


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import db  # noqa: E402
from models.platform_credential import PlatformCredentialAccount  # noqa: E402
from services.platform_credential_service import (  # noqa: E402
    CredentialError,
    save_credential,
)
from services.zsxq_service import zsxq_service  # noqa: E402


class ZsxqCookieValidationTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            SECRET_KEY="test-secret",
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        PlatformCredentialAccount.__table__.create(bind=db.engine)

    def tearDown(self):
        db.session.remove()
        PlatformCredentialAccount.__table__.drop(bind=db.engine)
        self.ctx.pop()

    def test_save_rejects_zsxq_cookie_without_access_token(self):
        with self.assertRaisesRegex(
            CredentialError,
            "未检测到 zsxq_access_token",
        ):
            save_credential({
                "platform": "zsxq",
                "credential_type": "cookie_header",
                "name": "incomplete",
                "purposes": ["zsxq.sync"],
                "payload": "sensorsdata2015jssdkcross=analytics-only",
            })

        self.assertEqual(0, PlatformCredentialAccount.query.count())

    def test_mapping_cookie_json_is_normalized_for_runtime_use(self):
        cookie = zsxq_service.normalize_cookie(
            '{"zsxq_access_token":"token","other":"value"}'
        )

        self.assertIn("zsxq_access_token=token", cookie)
        self.assertIn("other=value", cookie)

    def test_valid_zsxq_cookie_can_be_saved(self):
        account = save_credential({
            "platform": "zsxq",
            "credential_type": "cookie_json",
            "name": "complete",
            "purposes": ["zsxq.sync"],
            "payload": {"zsxq_access_token": "token", "other": "value"},
        })

        self.assertEqual("active", account.status)
        self.assertIn("zsxq_access_token", account.masked_summary)


if __name__ == "__main__":
    unittest.main()
