"""Production security invariants and startup validation."""

from urllib.parse import urlparse


KNOWN_DEVELOPMENT_SECRETS = {
    '',
    'dev-secret-key-change-in-production',
    'change-me',
    'secret',
    'your-secret-key-here',
}


class SecurityConfigurationError(RuntimeError):
    """Raised when a production process would start with unsafe security settings."""


def _is_shared_rate_limit_storage(uri):
    scheme = urlparse(str(uri or '')).scheme.lower()
    return scheme in {'redis', 'rediss', 'memcached', 'mongodb'}


def validate_security_config(config):
    """Fail closed when production authentication invariants are not met."""
    if str(config.get('AUTH_MODE') or '').lower() != 'production':
        return

    errors = []
    secret_key = str(config.get('SECRET_KEY') or '')
    jwt_secret = str(config.get('JWT_SECRET_KEY') or '')
    portal_token = str(config.get('LOGIN_PORTAL_TOKEN') or '')
    cors_origins = list(config.get('CORS_ORIGINS') or [])

    if config.get('DEBUG'):
        errors.append('DEBUG must be disabled')
    if config.get('LOCAL_AUTH_BYPASS'):
        errors.append('LOCAL_AUTH_BYPASS must be disabled')
    if not config.get('AUTH_VERSION_ENABLED'):
        errors.append('AUTH_VERSION_ENABLED must be enabled')
    if len(secret_key) < 32 or secret_key.lower() in KNOWN_DEVELOPMENT_SECRETS:
        errors.append('SECRET_KEY must be a unique random value of at least 32 characters')
    if len(jwt_secret) < 32 or jwt_secret.lower() in KNOWN_DEVELOPMENT_SECRETS:
        errors.append('JWT_SECRET_KEY must be a unique random value of at least 32 characters')
    if jwt_secret == secret_key:
        errors.append('JWT_SECRET_KEY must differ from SECRET_KEY')
    if len(portal_token) < 32:
        errors.append('LOGIN_PORTAL_TOKEN must contain at least 32 random characters')
    if not config.get('SESSION_COOKIE_SECURE'):
        errors.append('SESSION_COOKIE_SECURE must be enabled')
    if not config.get('SESSION_COOKIE_HTTPONLY'):
        errors.append('SESSION_COOKIE_HTTPONLY must be enabled')
    if str(config.get('SESSION_COOKIE_SAMESITE') or '').lower() not in {'strict', 'lax'}:
        errors.append('SESSION_COOKIE_SAMESITE must be Strict or Lax')
    if not cors_origins or '*' in cors_origins:
        errors.append('CORS_ORIGINS must be an explicit non-empty allowlist')
    if config.get('PUBLIC_REGISTRATION_ENABLED'):
        errors.append('PUBLIC_REGISTRATION_ENABLED must be disabled')
    if not _is_shared_rate_limit_storage(config.get('RATELIMIT_STORAGE_URL')):
        errors.append('RATELIMIT_STORAGE_URL must use shared production storage')

    if errors:
        message = '; '.join(errors)
        raise SecurityConfigurationError(f'Unsafe production configuration: {message}')
