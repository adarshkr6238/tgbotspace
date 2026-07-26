import logging
from bot.utils.progress import safe_edit
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)

async def handle_remove_streams(client, task, indices):
    task["preset_override"] = f"edit_stream_{'_'.join(map(str, indices))}"
    task["is_editing"] = False
    
    markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{task['status_msg'].id}")]]
    )
    await safe_edit(task["status_msg"], 
        f"📝 Stream removal registered for indices: {indices}. Processing...", reply_markup=markup
    )
    # The actual processing happens in compression_stage when continue_task is called
    return True

async def handle_extract_streams(client, task, indices):
    # The actual processing happens in compression_stage when continue_task is called
    return True

async def handle_video_trim(client, task, queue_manager):
    task["is_editing"] = True
    queue_manager.set_edit_state(task["user_id"], "WAITING_FOR_TRIM_TIMES", task["status_msg"].id)
    
    markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{task['status_msg'].id}")]]
    )
    await safe_edit(task["status_msg"], 
        "✂️ Please send the start and end times in the format: `HH:MM:SS HH:MM:SS`\nExample: `00:00:10 00:00:30`", reply_markup=markup
    )
    return True
