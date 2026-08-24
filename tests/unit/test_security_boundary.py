import os
import sys
import unittest
from unittest.mock import patch


ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
BACKEND = os.path.join(ROOT, 'src', 'backend')
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from utils.security_config import SecurityConfigurationError, validate_security_config
from config.production import ProductionConfig


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

    def test_business_page_is_hidden_without_authentication(self):
        self.assertEqual(self.app.test_client().get('/index.html').status_code, 404)

    def test_business_api_is_hidden_without_authentication(self):
        self.assertEqual(self.app.test_client().get('/api/v1/channels').status_code, 404)

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
