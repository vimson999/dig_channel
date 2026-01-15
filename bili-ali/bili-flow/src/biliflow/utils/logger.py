import sys
from loguru import logger
from biliflow.config.settings import settings

def setup_logger():
    """
    Configure the global logger instance.
    """
    logger.remove() # Remove default handler
    
    # Console Handler
    logger.add(
        sys.stderr,
        level=settings.LOG_LEVEL,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
    )
    
    # File Handler (Optional: Rotate every 10 MB)
    logger.add(
        "logs/biliflow.log",
        rotation="10 MB",
        retention="10 days",
        level="DEBUG", # Always capture debug logs in file
        compression="zip"
    )

    return logger

# Initialize logger
log = setup_logger()
