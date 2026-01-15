import os
from biliflow.config.settings import Settings

def test_settings_load_defaults():
    """Test that settings load with default values."""
    settings = Settings()
    assert settings.LOG_LEVEL == "INFO"

def test_settings_env_override():
    """Test that environment variables override defaults."""
    os.environ["LOG_LEVEL"] = "DEBUG"
    # Reload settings
    settings = Settings()
    assert settings.LOG_LEVEL == "DEBUG"
    # Clean up
    del os.environ["LOG_LEVEL"]
