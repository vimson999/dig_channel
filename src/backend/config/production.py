"""Strict production configuration."""

from .base import BaseConfig


class ProductionConfig(BaseConfig):
    DEBUG = False
    TESTING = False
    AUTH_MODE = 'production'
    LOCAL_AUTH_BYPASS = False
    AUTH_REQUIRE_ADMIN = True
    AUTH_VERSION_ENABLED = True
    PUBLIC_REGISTRATION_ENABLED = False
    SESSION_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Strict'
