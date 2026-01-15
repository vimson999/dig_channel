from typing import Optional
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field

class Settings(BaseSettings):
    """
    Application Configuration
    
    Loads settings from environment variables and .env file.
    """
    
    # Bilibili API Credentials
    # Optional because some public endpoints don't strictly require them,
    # but strictly needed for higher privileges.
    SESSDATA: Optional[str] = Field(default=None, description="Bilibili SESSDATA cookie")
    BILI_JCT: Optional[str] = Field(default=None, description="Bilibili BILI_JCT cookie")
    BUVID3: Optional[str] = Field(default=None, description="Bilibili BUVID3 cookie")
    DEDEUSERID: Optional[str] = Field(default=None, description="Bilibili DEDEUSERID cookie")

    # App Settings
    LOG_LEVEL: str = Field(default="INFO", description="Logging level (DEBUG, INFO, WARNING, ERROR)")
    
    model_config = SettingsConfigDict(
        env_file=".env", 
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore" # Ignore extra fields in .env
    )

# Singleton instance
settings = Settings()
