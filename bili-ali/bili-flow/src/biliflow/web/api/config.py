import os
from pathlib import Path
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()

ENV_PATH = Path(".env")
EXAMPLE_ENV_PATH = Path(".env.example")

class ConfigModel(BaseModel):
    SESSDATA: str
    BILI_JCT: str
    BUVID3: str
    DEDEUSERID: str

@router.get("/config")
async def get_config():
    """
    Reads the .env file. If it doesn't exist, returns empty strings or defaults.
    """
    config = {
        "SESSDATA": "",
        "BILI_JCT": "",
        "BUVID3": "",
        "DEDEUSERID": "",
        "exists": False
    }
    
    if ENV_PATH.exists():
        config["exists"] = True
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    if key in config:
                        config[key] = value
    return config

@router.post("/config")
async def save_config(data: ConfigModel):
    """
    Saves the .env file.
    """
    content = f"""SESSDATA={data.SESSDATA}
BILI_JCT={data.BILI_JCT}
BUVID3={data.BUVID3}
DEDEUSERID={data.DEDEUSERID}
LOG_LEVEL=INFO
"""
    try:
        with open(ENV_PATH, "w", encoding="utf-8") as f:
            f.write(content)
        return {"status": "success", "message": "Configuration saved."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
