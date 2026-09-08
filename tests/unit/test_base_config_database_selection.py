import importlib.util
import os
from pathlib import Path
from unittest import mock
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASE_CONFIG_PATH = PROJECT_ROOT / "src" / "backend" / "config" / "base.py"


def load_base_config(env):
    spec = importlib.util.spec_from_file_location("isolated_base_config", BASE_CONFIG_PATH)
    module = importlib.util.module_from_spec(spec)
    with mock.patch.dict(os.environ, env, clear=True), mock.patch("dotenv.load_dotenv"):
        spec.loader.exec_module(module)
    return module.BaseConfig


class BaseConfigDatabaseSelectionTest(unittest.TestCase):
    def test_default_remains_local_sqlite_with_legacy_tts_disabled(self):
        config = load_base_config({})

        self.assertTrue(config.SQLALCHEMY_DATABASE_URI.startswith("sqlite:///"))
        self.assertFalse(config.ENABLE_TTS_API)

    def test_postgresql_requires_explicit_opt_in(self):
        config = load_base_config(
            {
                "USE_POSTGRESQL": "true",
                "POSTGRES_HOST": "db.example.test",
                "POSTGRES_PORT": "5433",
                "POSTGRES_DB": "analytics",
                "POSTGRES_USER": "app",
                "POSTGRES_PASSWORD": "secret",
            }
        )

        self.assertEqual(
            config.SQLALCHEMY_DATABASE_URI,
            "postgresql://app:secret@db.example.test:5433/analytics",
        )

    def test_database_url_takes_precedence(self):
        config = load_base_config(
            {
                "USE_POSTGRESQL": "false",
                "DATABASE_URL": "postgresql://explicit.example.test/app",
            }
        )

        self.assertEqual(
            config.SQLALCHEMY_DATABASE_URI,
            "postgresql://explicit.example.test/app",
        )


if __name__ == "__main__":
    unittest.main()
