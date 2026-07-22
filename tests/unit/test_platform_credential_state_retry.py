import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from sqlalchemy.exc import OperationalError


BACKEND_DIR = Path(__file__).resolve().parents[2] / "src" / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from models import db  # noqa: E402
from models.platform_credential import PlatformCredentialAccount  # noqa: E402
from services import platform_credential_service as credential_service  # noqa: E402
from services.platform_credential_service import mark_credential_success  # noqa: E402


class PlatformCredentialStateRetryTest(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(
            SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
            SQLALCHEMY_TRACK_MODIFICATIONS=False,
        )
        db.init_app(self.app)
        self.ctx = self.app.app_context()
        self.ctx.push()
        PlatformCredentialAccount.__table__.create(bind=db.engine)
        self.account = PlatformCredentialAccount(
            id="credential-1",
            platform="zsxq",
            name="publish account",
            credential_type="cookie_header",
            purposes=["zsxq.publish"],
            status="expired",
            encrypted_payload="encrypted",
            payload_fingerprint="fingerprint-1",
        )
        db.session.add(self.account)
        db.session.commit()
        credential_service._invalidate_credential_resolution_cache()

    def tearDown(self):
        credential_service._invalidate_credential_resolution_cache()
        db.session.remove()
        PlatformCredentialAccount.__table__.drop(bind=db.engine)
        self.ctx.pop()

    @staticmethod
    def _disconnect_error(*, invalidated):
        return OperationalError(
            "UPDATE platform_credential_accounts",
            {},
            RuntimeError("server closed the connection unexpectedly"),
            connection_invalidated=invalidated,
        )

    def test_success_marker_retries_once_after_invalidated_connection(self):
        real_commit = db.session.commit
        commit_attempts = 0

        def flaky_commit():
            nonlocal commit_attempts
            commit_attempts += 1
            if commit_attempts == 1:
                raise self._disconnect_error(invalidated=True)
            return real_commit()

        with patch.object(db.session, "commit", side_effect=flaky_commit), patch.object(
            db.session,
            "rollback",
            wraps=db.session.rollback,
        ) as rollback:
            mark_credential_success(self.account.id)

        db.session.expire_all()
        refreshed = db.session.get(PlatformCredentialAccount, self.account.id)
        self.assertEqual("active", refreshed.status)
        self.assertIsNone(refreshed.last_error)
        self.assertEqual(2, commit_attempts)
        rollback.assert_called_once_with()

    def test_success_marker_does_not_retry_non_invalidated_db_error(self):
        error = self._disconnect_error(invalidated=False)

        with patch.object(db.session, "commit", side_effect=error) as commit:
            with self.assertRaises(OperationalError):
                mark_credential_success(self.account.id)

        commit.assert_called_once_with()

    def test_explicit_resolution_is_single_flight_and_returns_isolated_snapshots(self):
        snapshot = SimpleNamespace(id="credential-1", extra={"label": "original"})
        with patch.object(
            credential_service,
            "_resolve_credential_record_uncached",
            return_value=(snapshot, "secret"),
        ) as uncached:
            first, first_payload = credential_service.resolve_credential_record(
                account_id="credential-1",
                platform="zsxq",
                purpose="zsxq.publish",
            )
            first.extra["label"] = "mutated"
            second, second_payload = credential_service.resolve_credential_record(
                account_id="credential-1",
                platform="zsxq",
                purpose="zsxq.publish",
            )

        self.assertEqual(1, uncached.call_count)
        self.assertEqual("secret", first_payload)
        self.assertEqual("secret", second_payload)
        self.assertEqual("original", second.extra["label"])

    def test_implicit_resolution_is_not_cached(self):
        snapshot = SimpleNamespace(id="credential-1")
        with patch.object(
            credential_service,
            "_resolve_credential_record_uncached",
            return_value=(snapshot, "secret"),
        ) as uncached:
            credential_service.resolve_credential_record(platform="zsxq", purpose="zsxq.publish")
            credential_service.resolve_credential_record(platform="zsxq", purpose="zsxq.publish")

        self.assertEqual(2, uncached.call_count)


if __name__ == "__main__":
    unittest.main()
