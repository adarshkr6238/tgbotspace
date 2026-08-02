import logging
import time

from bot.config.config import Config
from pyrogram.errors import FloodWait, MessageNotModified

logger = logging.getLogger(__name__)

_last_string = {}
_cancelled_tasks = set()
_last_update_time = {}
_is_updating = set()


def cancel_task(msg_id):
    _cancelled_tasks.add(msg_id)


def is_cancelled(msg_id):
    return msg_id in _cancelled_tasks


def clear_cancel_flag(msg_id):
    _cancelled_tasks.discard(msg_id)


async def safe_edit(message, text, reply_markup=None):
    try:
        await message.edit_text(text, reply_markup=reply_markup)
    except MessageNotModified:
        pass
    except FloodWait as e:
        await __import__("asyncio").sleep(e.value)
        try:
            await message.edit_text(text, reply_markup=reply_markup)
        except Exception:
            pass
    except Exception:
        pass


async def progress_bar(
    current,
    total,
    status_text,
    message,
    start_time,
    last_update_time=0, # Kept for backwards compatibility but ignored
    task=None,
    reply_markup=None,
):
    global _last_string, _last_update_time, _is_updating
    msg_id = message.id

    if msg_id in _cancelled_tasks:
        raise Exception("CANCELLED")

    now = time.time()
    last_time = _last_update_time.get(msg_id, 0)

    # Fast synchronous check to drop excessive concurrent calls instantly
    if now - last_time < Config.PROGRESS_UPDATE_INTERVAL and current != total:
        return now
        
    if msg_id in _is_updating and current != total:
        return now
        
    _is_updating.add(msg_id)
    _last_update_time[msg_id] = now

    percentage = current * 100 / total if total else 0

    if task is not None:
        task["percentage"] = percentage

    elapsed = now - start_time
    speed = current / elapsed if elapsed > 0 else 0
    eta = (total - current) / speed if speed > 0 else 0

    filled_length = int(10 * current // total) if total else 0
    bar = "█" * filled_length + "░" * (10 - filled_length)

    progress_str = (
        f"**{status_text}**\n"
        f"[{bar}] {percentage:.1f}%\n"
        f"🚀 Speed: {format_bytes(speed)}/s\n"
        f"⏳ ETA: {format_time(eta)}\n"
        f"⏱️ Elapsed: {format_time(elapsed)}"
    )

    if _last_string.get(msg_id) == progress_str and current != total:
        _is_updating.discard(msg_id)
        return now

    _last_string[msg_id] = progress_str

    # Check if we should even update. If the current reply_markup is an edit menu, 
    # we might want to avoid overriding it with progress bar updates.
    # We can detect this if the user passed a custom markup.
    
    # If the user passed a specific markup (like the edit menu), we should keep it.
    # Only if it's the default 'Cancel' button, we can potentially overwrite it.
    
    if reply_markup is not None:
        # Keep the provided markup
        markup = reply_markup
    else:
        # Default markup
        from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{msg_id}")]]
        )
        
    # Check if the current message already has an edit menu that we shouldn't overwrite.
    # A simple heuristic: if it's the download progress bar, we can overwrite.
    # But if the current progress_str is not just for download, maybe we shouldn't.
    
    # Actually, the user's issue is that the progress bar updates are *overwriting* 
    # their edit menu. Let's make sure we only update if it's a progress update.
    
    # To fix the user's issue: if an edit menu is active (task['is_editing'] is True),
    # we should NOT update the message with progress bar.
    
    if task and task.get("is_editing"):
        _is_updating.discard(msg_id)
        return now

    try:
        await safe_edit(message, progress_str, reply_markup=markup)
    except Exception:
        pass
    finally:
        _is_updating.discard(msg_id)

    if current == total:
        _last_string.pop(msg_id, None)
        _last_update_time.pop(msg_id, None)

    return now


def truncate_text(text, max_len=3000):
    if len(text) <= max_len:
        return text
    return text[: max_len - 50] + "\n\n... (Text Truncated) ..."


def format_bytes(size):
    power = 2**10
    n = 0
    power_labels = {0: "", 1: "K", 2: "M", 3: "G", 4: "T"}
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
