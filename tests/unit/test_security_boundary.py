import os
import sys
import hashlib
import hmac
import json
import time
import unittest
from unittest.mock import patch


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKEND = os.path.join(ROOT, 'src', 'backend')
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from utils.security_config import SecurityConfigurationError, validate_security_config
from config.production import ProductionConfig
from models import db
from services.zsxq_history_api_client_service import create_history_api_client


def valid_production_config():
    return {
        'AUTH_MODE': 'production',
        'DEBUG': False,
        'LOCAL_AUTH_BYPASS': False,
        'AUTH_VERSION_ENABLED': True,
        'SECRET_KEY': 's' * 48,
        'JWT_SECRET_KEY': 'j' * 48,
        'LOGIN_PORTAL_TOKEN': 'p' * 48,
        'SESSION_COOKIE_SECURE': True,
        'SESSION_COOKIE_HTTPONLY': True,
        'SESSION_COOKIE_SAMESITE': 'Strict',
        'CORS_ORIGINS': ['https://private.example.com'],
        'PUBLIC_REGISTRATION_ENABLED': False,
        'RATELIMIT_STORAGE_URL': 'redis://redis:6379/0',
    }


class SecurityConfigurationTests(unittest.TestCase):
    def test_valid_production_config_passes(self):
        validate_security_config(valid_production_config())

    def test_production_local_bypass_fails_closed(self):
        config = valid_production_config()
        config['LOCAL_AUTH_BYPASS'] = True
        with self.assertRaises(SecurityConfigurationError):
            validate_security_config(config)

    def test_history_client_credentials_are_not_process_security_config(self):
        config = valid_production_config()
        config['ZSXQ_HISTORY_API_KEY'] = 'legacy-value'
        config['ZSXQ_HISTORY_API_SECRET'] = 'legacy-value'
        validate_security_config(config)

class TestProduction(ProductionConfig):
    TESTING = True
    SECRET_KEY = 's' * 48
    JWT_SECRET_KEY = 'j' * 48
    LOGIN_PORTAL_TOKEN = 'p' * 48
    CORS_ORIGINS = ['https://private.example.com']
    RATELIMIT_STORAGE_URL = 'redis://redis:6379/0'
    RATELIMIT_STORAGE_URI = RATELIMIT_STORAGE_URL
    RATELIMIT_ENABLED = False
    SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'
    SQLALCHEMY_ENGINE_OPTIONS = {}


class ProductionBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app import create_app
        cls.app = create_app(TestProduction)
        cls.app_context = cls.app.app_context()
        cls.app_context.push()
        db.create_all()
        cls.history_client = create_history_api_client(
            tenant_id='boundary-test-tenant',
            name='boundary-test-client',
        )

    @classmethod
    def tearDownClass(cls):
        db.session.remove()
        cls.app_context.pop()

    def test_business_page_is_hidden_without_authentication(self):
        self.assertEqual(self.app.test_client().get('/index.html').status_code, 404)

    def test_business_api_is_hidden_without_authentication(self):
        self.assertEqual(self.app.test_client().get('/api/v1/channels').status_code, 404)

    def test_allowed_origin_can_read_insights_cache_headers(self):
        response = self.app.test_client().get(
            '/health',
            headers={'Origin': 'https://private.example.com'},
        )

        self.assertEqual(
            response.headers.get('Access-Control-Allow-Origin'),
            'https://private.example.com',
        )
        exposed = {
            value.strip()
            for value in response.headers.get('Access-Control-Expose-Headers', '').split(',')
            if value.strip()
        }
        self.assertTrue(
            {
                'X-Insights-Cache',
                'X-Insights-Cache-Tier',
                'X-Insights-Refresh',
            }.issubset(exposed)
        )

    def test_signed_zsxq_history_request_bypasses_interactive_login(self):
        from unittest.mock import patch

        app_id = self.history_client['app_id']
        api_secret = self.history_client['secret']
        payload = {
            'kind': 'mp3',
            'days': 3,
            'group_id': '28888112822211',
            'tab_id': '88844545452542',
        }
        body = json.dumps(payload, separators=(',', ':')).encode('utf-8')
        timestamp = str(int(time.time()))
        signing_string = '\n'.join([
            app_id,
            timestamp,
            'POST',
            '/api/v1/zsxq/browser-import/history',
            hashlib.sha256(body).hexdigest(),
        ])
        signature = hmac.new(
            api_secret.encode('utf-8'),
            signing_string.encode('utf-8'),
            hashlib.sha256,
        ).hexdigest()
        with patch(
            'apis.v1.zsxq_browser_import.zsxq_browser_import_service.recent_download_history',
            return_value={'kind': 'mp3', 'days': 3, 'complete': True, 'files': []},
        ):
            response = self.app.test_client().post(
                '/api/v1/zsxq/browser-import/history',
                data=body,
                content_type='application/json',
                headers={
                    'X-App-Id': app_id,
                    'X-Timestamp': timestamp,
                    'X-Signature': signature,
                },
                environ_base={'REMOTE_ADDR': '203.0.113.10'},
            )

        self.assertEqual(response.status_code, 200)

    def test_route_auth_reuses_user_resolved_by_project_guard(self):
        from flask import g
        from utils import auth_utils

        resolved_user = object()
        with self.app.test_request_context('/api/v1/channels'):
            g.current_user = resolved_user
            with patch.object(
                auth_utils,
                'get_user_from_token',
                side_effect=AssertionError('must reuse request user'),
            ), patch.object(
                auth_utils,
                'get_user_from_session',
                side_effect=AssertionError('must reuse request user'),
            ):
                self.assertIs(resolved_user, auth_utils.get_current_user())

    def test_direct_login_filename_is_hidden(self):
        self.assertEqual(self.app.test_client().get('/login.html').status_code, 404)

    def test_wrong_private_portal_is_hidden(self):
        self.assertEqual(self.app.test_client().get('/access/wrong').status_code, 404)

    def test_private_portal_sets_no_store_response(self):
        response = self.app.test_client().get('/access/' + ('p' * 48))
        self.assertEqual(response.status_code, 200)
        self.assertIn('no-store', response.headers.get('Cache-Control', ''))
        response.close()

    def test_production_wildcard_cors_fails_closed(self):
        config = valid_production_config()
        config['CORS_ORIGINS'] = ['*']
        with self.assertRaises(SecurityConfigurationError):
            validate_security_config(config)

    def test_production_requires_shared_rate_limit_storage(self):
        config = valid_production_config()
        config['RATELIMIT_STORAGE_URL'] = 'memory://'
        with self.assertRaises(SecurityConfigurationError):
            validate_security_config(config)


if __name__ == '__main__':
    unittest.main()
