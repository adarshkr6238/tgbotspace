import time
import logging
from pyrogram.errors import FloodWait
from bot.config.config import Config

logger = logging.getLogger(__name__)

_last_string = {}
_cancelled_tasks = set()

def cancel_task(msg_id):
    _cancelled_tasks.add(msg_id)

def is_cancelled(msg_id):
    return msg_id in _cancelled_tasks

def clear_cancel_flag(msg_id):
    _cancelled_tasks.discard(msg_id)

async def progress_bar(current, total, status_text, message, start_time, last_update_time, task=None, reply_markup=None):
    global _last_string
    msg_id = message.id

    if msg_id in _cancelled_tasks:
        raise Exception("CANCELLED")

    now = time.time()
    percentage = current * 100 / total if total else 0
    
    if task is not None:
        task['percentage'] = percentage

    if now - last_update_time < Config.PROGRESS_UPDATE_INTERVAL and current != total:
        return last_update_time

    elapsed = now - start_time
    speed = current / elapsed if elapsed > 0 else 0
    eta = (total - current) / speed if speed > 0 else 0

    filled_length = int(10 * current // total) if total else 0
    bar = '█' * filled_length + '░' * (10 - filled_length)

    progress_str = (
        f"**{status_text}**\n"
        f"[{bar}] {percentage:.1f}%\n"
        f"🚀 Speed: {format_bytes(speed)}/s\n"
        f"⏳ ETA: {format_time(eta)}\n"
        f"⏱️ Elapsed: {format_time(elapsed)}"
    )

    if _last_string.get(msg_id) == progress_str and current != total:
        return now
    
    _last_string[msg_id] = progress_str

    try:
        from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        markup = reply_markup or InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{msg_id}")]])
        await message.edit_text(
            progress_str,
            reply_markup=markup
        )
    except FloodWait as e:
        logger.warning(f"FloodWait in progress bar: sleeping for {e.value}s in background")
        # We don't sleep here to avoid blocking the download/compress task, 
        # we just skip updating the UI for this tick.
    except Exception:
        pass
    
    if current == total:
        _last_string.pop(msg_id, None)
        
    return now

def truncate_text(text, max_len=3000):
    if len(text) <= max_len:
        return text
    return text[:max_len-50] + "\n\n... (Text Truncated) ..."

def format_bytes(size):
    power = 2**10
    n = 0
    power_labels = {0: '', 1: 'K', 2: 'M', 3: 'G', 4: 'T'}
    while size > power:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels[n]}B"

def format_time(seconds):
    if seconds < 0:
        return "00:00"
    minutes, seconds = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
    return f"{minutes:02d}:{seconds:02d}"
