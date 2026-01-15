from fastapi import APIRouter
from bilibili_api import user, Credential
from biliflow.config.settings import Settings # Load fresh settings
import os

router = APIRouter()

@router.get("/status/login")
async def check_login_status():
    """
    Checks if the current credentials in .env are valid.
    Force reload settings to ensure we use the latest .env
    """
    # Force reload settings from environment/file
    # Note: pydantic BaseSettings loads on instantiation. 
    # To refresh, we need to create a new instance or reload.
    # For this simple check, we will manually read env vars set by the load process 
    # OR simpler: just re-instantiate Settings logic or read the file.
    
    # Actually, Config module writes to .env. We need to make sure 'settings' object picks it up.
    # Simplest way for this check is to manually read the .env file again or assume the user has just saved it.
    
    # Let's read directly from .env for this check to be sure
    from dotenv import dotenv_values
    env_config = dotenv_values(".env")
    
    sessdata = env_config.get("SESSDATA")
    bili_jct = env_config.get("BILI_JCT")
    buvid3 = env_config.get("BUVID3")
    dedeuserid = env_config.get("DEDEUSERID")

    if not (sessdata and bili_jct and buvid3 and dedeuserid):
        return {"valid": False, "message": "Missing credentials in .env"}

    credential = Credential(
        sessdata=sessdata,
        bili_jct=bili_jct,
        buvid3=buvid3,
        dedeuserid=dedeuserid
    )

    try:
        u = user.User(uid=int(dedeuserid), credential=credential)
        info = await u.get_user_info()
        return {
            "valid": True, 
            "username": info.get('name'),
            "level": info.get('level'),
            "face": info.get('face')
        }
    except Exception as e:
        return {"valid": False, "message": str(e)}
