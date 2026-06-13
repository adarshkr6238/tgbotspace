import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    API_ID = int(os.getenv("API_ID", "0"))
    API_HASH = os.getenv("API_HASH", "")
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    OWNER_ID = int(os.getenv("OWNER_ID", "0"))
    
    # Authorized users
    AUTH_USERS = set(int(x) for x in os.getenv("AUTH_USERS", "").split(",") if x.strip())
    AUTH_USERS.add(OWNER_ID)
    
    # Storage settings (Local paths are more stable on HF)
    DOWNLOAD_DIR = "downloads"
    TEMP_DIR = "work_temp"
    PUBLIC_DIR = "public_downloads"
    
    # FFmpeg presets (Logic handled in ffmpeg_service.py)
    PRESETS = {
        "low": {"desc": "Max 450p, high quality bitrate"},
        "medium": {"desc": "Max 360p, balanced bitrate"},
        "high": {"desc": "Max 240p, small file"}
    }
    
    DEFAULT_PRESET = "medium"
    
    # Progress bar update frequency (seconds)
    PROGRESS_UPDATE_INTERVAL = 6
    
    # Queue settings
    MAX_QUEUE_SIZE = 70
