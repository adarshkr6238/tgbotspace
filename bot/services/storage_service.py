import os
import time
import shutil
import logging
from bot.config.config import Config

logger = logging.getLogger(__name__)

def setup_storage():
    for d in [Config.DOWNLOAD_DIR, Config.TEMP_DIR, Config.PUBLIC_DIR]:
        os.makedirs(d, exist_ok=True)

def cleanup_old_files():
    # Only delete files older than 40 minutes to avoid killing active tasks
    # 40 minutes = 2400 seconds
    now = time.time()
    cutoff_temp = now - 2400
    cutoff_public = now - 10800 # 3 hours for public links
    
    for d, cutoff in [(Config.DOWNLOAD_DIR, cutoff_temp), (Config.TEMP_DIR, cutoff_temp), (Config.PUBLIC_DIR, cutoff_public)]:
        if not os.path.exists(d):
            continue
        for f in os.listdir(d):
            f_path = os.path.join(d, f)
            try:
                # Get file age
                file_time = os.path.getmtime(f_path)
                if file_time < cutoff:
                    if os.path.isfile(f_path) or os.path.islink(f_path):
                        os.unlink(f_path)
                    elif os.path.isdir(f_path):
                        shutil.rmtree(f_path)
            except Exception as e:
                logger.error(f"Cleanup error for {f_path}: {e}")

def wipe_all_storage():
    # Aggressive wipe: delete EVERYTHING in storage dirs instantly
    for d in [Config.DOWNLOAD_DIR, Config.TEMP_DIR, Config.PUBLIC_DIR]:
        if not os.path.exists(d):
            continue
        for f in os.listdir(d):
            f_path = os.path.join(d, f)
            try:
                if os.path.isfile(f_path) or os.path.islink(f_path):
                    os.unlink(f_path)
                elif os.path.isdir(f_path):
                    shutil.rmtree(f_path)
            except Exception as e:
                logger.error(f"Wipe error for {f_path}: {e}")
