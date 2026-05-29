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
        
        # Interactive finish button
        finish_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Finish & Merge", callback_data=f"edit_finishmerge_{msg_id}")],
            [InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{msg_id}")]
        ])
        
        await callback_query.message.edit_text(
            "🔗 **Video Merger**\n\n"
            "1. Send the next video you want to merge.\n"
            "2. Repeat for all videos.\n"
            "3. Click **Finish & Merge** below when done.\n\n"
            "*First video is already registered.*",
            reply_markup=finish_markup
        )
    elif action == "split":
        # Interactive number pad for split parts
        split_markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("2 Parts", callback_data=f"edit_dosplit_2_{msg_id}"),
             InlineKeyboardButton("3 Parts", callback_data=f"edit_dosplit_3_{msg_id}")],
            [InlineKeyboardButton("4 Parts", callback_data=f"edit_dosplit_4_{msg_id}"),
             InlineKeyboardButton("5 Parts", callback_data=f"edit_dosplit_5_{msg_id}")],
            [InlineKeyboardButton("6 Parts", callback_data=f"edit_dosplit_6_{msg_id}"),
             InlineKeyboardButton("10 Parts", callback_data=f"edit_dosplit_10_{msg_id}")],
            [InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{msg_id}")]
        ])
        
        await callback_query.message.edit_text(
            "🔪 **Video Splitter**\n\n"
            "Select how many equal parts you want to split this video into:",
            reply_markup=split_markup
        )
    elif action == "finishmerge":
        state = queue_manager.get_edit_state(task['user_id'])
        if not state or state['msg_id'] != msg_id:
            await callback_query.answer("❌ Invalid or expired merge session.", show_alert=True)
            return
            
        if len(state['files']) < 1:
            await callback_query.answer("⚠️ You haven't sent any additional videos to merge.", show_alert=True)
            return
            
        task['merge_files'] = [f['file_id'] for f in state['files']]
        queue_manager.clear_edit_state(task['user_id'])
        
        # Proceed to queue
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{msg_id}")]])
        await callback_query.message.edit_text(
            f"📝 Merge compilation finished. Added to queue (Position: {queue_manager.get_position(task)}).",
            reply_markup=markup
        )
        
    elif action.startswith("dosplit_"):
        parts = int(action.split("_")[1])
        task['preset_override'] = f"edit_split_{parts}"
        queue_manager.clear_edit_state(task['user_id'])
        
        # Proceed to queue
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{msg_id}")]])
        await callback_query.message.edit_text(
            f"📝 Split into {parts} parts registered. Added to queue (Position: {queue_manager.get_position(task)}).",
            reply_markup=markup
        )
        
    else:
        await callback_query.answer("⚠️ Feature backend logic is still being connected. Coming soon!", show_alert=True)
