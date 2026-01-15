import asyncio
import sys
from pathlib import Path

# Add src to path
sys.path.append(str(Path(__file__).parent.parent / "src"))

from bilibili_api import user, Credential
from biliflow.config.settings import settings
from biliflow.utils.logger import log

async def main():
    log.info("Checking Bilibili credentials...")
    
    if not settings.SESSDATA:
        log.error("SESSDATA is missing in .env file!")
        return

    sess = Credential(
        sessdata=settings.SESSDATA,
        bili_jct=settings.BILI_JCT,
        buvid3=settings.BUVID3,
        dedeuserid=settings.DEDEUSERID
    )
    
    try:
        # Get own user info
        u = user.User(uid=int(settings.DEDEUSERID), credential=sess)
        info = await u.get_user_info()
        log.success(f"Login valid! User: {info['name']} (Level: {info['level']})")
    except Exception as e:
        log.error(f"Login check failed: {e}")

if __name__ == "__main__":
    if not settings.DEDEUSERID:
        log.error("DEDEUSERID is missing in .env file, cannot check user info.")
    else:
        asyncio.run(main())
