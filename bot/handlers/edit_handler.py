from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

def get_edit_menu_markup(msg_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("✂️ Stream Remover", callback_data=f"edit_stream_{msg_id}")],
        [InlineKeyboardButton("🔗 Video Merger", callback_data=f"edit_vmerge_{msg_id}")],
        [InlineKeyboardButton("🎶 Audio/Video Merger", callback_data=f"edit_avmerge_{msg_id}")],
        [InlineKeyboardButton("🔪 Video Splitter", callback_data=f"edit_split_{msg_id}")],
        [InlineKeyboardButton("📝 Video Renamer", callback_data=f"edit_rename_{msg_id}")],
        [InlineKeyboardButton("🔙 Back", callback_data=f"edit_back_{msg_id}")]
    ])

async def handle_edit_menu(client, callback_query, queue_manager):
    msg_id = int(callback_query.data.split("_")[1])
    task = queue_manager.all_tasks.get(msg_id)
    if not task:
        await callback_query.answer("❌ Task not found.", show_alert=True)
        return

    await callback_query.message.edit_text(
        "🛠 **Edit File Menu**\n\nChoose an editing option. This will bypass normal compression.",
        reply_markup=get_edit_menu_markup(msg_id)
    )

async def handle_edit_action(client, callback_query, queue_manager):
    action = callback_query.data.split("_")[1]
    msg_id = int(callback_query.data.split("_")[2])
    task = queue_manager.all_tasks.get(msg_id)
    
    if not task:
        await callback_query.answer("❌ Task not found.", show_alert=True)
        return

    if action == "back":
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("✨ /diff Quality Mode", callback_data=f"diff_{msg_id}")],
            [InlineKeyboardButton("✏️ Edit File", callback_data=f"editmenu_{msg_id}")],
            [InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{msg_id}")]
        ])
        await callback_query.message.edit_text(
            f"📝 Added to queue (Position: {queue_manager.get_position(task)})\n\nShort videos (<= 5 min) get priority!",
            reply_markup=markup
        )
        return

    # Mark task as an edit task to bypass standard compression
    task['preset_override'] = f"edit_{action}"
    
    if action == "vmerge":
        queue_manager.set_edit_state(task['user_id'], 'WAITING_FOR_MERGE_FILES', msg_id)
        await callback_query.message.edit_text(
            "🔗 **Video Merger**\n\n"
            "1. Send the next video you want to merge.\n"
            "2. Repeat for all videos.\n"
            "3. Send `/finish_merge` when done.\n\n"
            "*First video is already registered.*",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{msg_id}")]])
        )
    elif action == "split":
        queue_manager.set_edit_state(task['user_id'], 'WAITING_FOR_SPLIT_COUNT', msg_id)
        await callback_query.message.edit_text(
            "🔪 **Video Splitter**\n\n"
            "Reply to this message with the **number of parts** you want to split this video into (e.g., `3`).",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{msg_id}")]])
        )
    else:
        await callback_query.answer("⚠️ Feature backend logic is still being connected. Coming soon!", show_alert=True)
