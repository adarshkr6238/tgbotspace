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

async def handle_video_merge(client, task, queue_manager):
    task["is_editing"] = True
    queue_manager.set_edit_state(task["user_id"], "WAITING_FOR_MERGE_FILES", task["status_msg"].id)
    
    markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{task['status_msg'].id}")]]
    )
    await safe_edit(task["status_msg"], 
        "🎥 Please send the video files you want to merge (in order). Send /done when finished.", reply_markup=markup
    )
    return True

async def handle_video_mute(client, task, queue_manager):
    task["preset_override"] = "mute"
    task["is_editing"] = False
    
    markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{task['status_msg'].id}")]]
    )
    await safe_edit(task["status_msg"], 
        "🔇 Mute audio registered. Processing...", reply_markup=markup
    )
    # Trigger processing
    await queue_manager.continue_task(task)
    return True
