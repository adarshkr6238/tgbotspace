from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def build_stream_keyboard(streams, streams_to_remove, msg_id):
    buttons = []
    for s in streams:
        idx = s.get("index", 0)
        codec_type = s.get("codec_type", "unknown").upper()
        codec_name = s.get("codec_name", "")
        codec_long_name = s.get("codec_long_name", "")
        tags = s.get("tags", {})
        language = tags.get("language", "und").upper()
        title = tags.get("title", "")
        disposition = s.get("disposition", {})
        is_default = disposition.get("default", 0) == 1

        if codec_type == "AUDIO":
            lang_display = language
            if is_default:
                lang_display += " (Default)"

            codec_display = (
                f"{codec_long_name} ({codec_name})" if codec_long_name else codec_name
            )

            channels = s.get("channels", "")
            layout = s.get("channel_layout", "")
            sr = s.get("sample_rate", "")
            br = s.get("bit_rate", "")

            audio_details = codec_display
            if layout:
                audio_details += f" {layout}"
            elif channels:
                audio_details += f" {channels}ch"

            if br:
                try:
                    kbps = int(int(br) / 1000)
                    audio_details += f" @ {kbps} kbps"
                except:
                    pass

            if sr:
                try:
                    khz = int(sr) // 1000
                    audio_details += f", {khz}kHz"
                except:
                    pass

            label = f"{lang_display} - {audio_details}"
            if title:
                label = f"{lang_display} ({title}) - {audio_details}"

        elif codec_type == "SUBTITLE":
            label = f"[SUB] {language}"
            if is_default:
                label += " (Default)"
            if title:
                label += f" - {title}"
        else:
            label = f"[{codec_type}] {language}"
            if title:
                label += f" - {title}"

        if idx in streams_to_remove:
            label = f"✅ Remove: {label}"
        else:
            label = f"Keep: {label}"

        buttons.append(
            [
                InlineKeyboardButton(
                    label, callback_data=f"edit_togglestream_{idx}_{msg_id}"
                )
            ]
        )

    buttons.append(
        [
            InlineKeyboardButton(
                "▶️ Finish & Remove Selected",
                callback_data=f"edit_finishstream_{msg_id}",
            )
        ]
    )
    buttons.append(
        [InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{msg_id}")]
    )
    return InlineKeyboardMarkup(buttons)


def get_edit_menu_markup(msg_id):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "🔗 File to Link", callback_data=f"edit_link_{msg_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "🎬 Sample Generator", callback_data=f"edit_sample_{msg_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "✂️ Stream Remover", callback_data=f"edit_stream_{msg_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "🔗 Video Merger", callback_data=f"edit_vmerge_{msg_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "🎶 Audio/Video Merger", callback_data=f"edit_avmerge_{msg_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "🔪 Video Splitter", callback_data=f"edit_split_{msg_id}"
                )
            ],
            [
                InlineKeyboardButton(
                    "📝 Video Renamer", callback_data=f"edit_rename_{msg_id}"
                )
            ],
            [InlineKeyboardButton("🔙 Back", callback_data=f"edit_back_{msg_id}")],
        ]
    )


async def handle_edit_menu(client, callback_query, queue_manager):
    msg_id = int(callback_query.data.split("_")[1])
    task = queue_manager.all_tasks.get(msg_id)
    if not task:
        await callback_query.answer("❌ Task not found.", show_alert=True)
        return

    task["is_editing"] = True

    await safe_edit(callback_query.message, 
        "🛠 **Edit File Menu**\n\nChoose an editing option. This will bypass normal compression.",
        reply_markup=get_edit_menu_markup(msg_id),
    )


async def handle_edit_action(client, callback_query, queue_manager):
    data_parts = callback_query.data.split("_")
    action = data_parts[1]
    msg_id = int(data_parts[-1])  # msg_id is always the last part
    task = queue_manager.all_tasks.get(msg_id)

    if not task:
        await callback_query.answer("❌ Task not found.", show_alert=True)
        return

    if action == "back":
        task["is_editing"] = False
        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✨ /diff Quality Mode", callback_data=f"diff_{msg_id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "✏️ Edit File", callback_data=f"editmenu_{msg_id}"
                    )
                ],
                [InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{msg_id}")],
            ]
        )
        await safe_edit(callback_query.message, 
            f"📝 Added to queue (Position: {queue_manager.get_position(task)})\n\nShort videos (<= 5 min) get priority!",
            reply_markup=markup,
        )
        
        # If download is already finished, resume it in the compression queue
        input_path = task.get("input_path")
        if input_path and os.path.exists(input_path):
            await queue_manager.continue_task(task)
        return

    # Mark task as an edit task to bypass standard compression
    task["preset_override"] = f"edit_{action}"

    if action in ["sample", "link"]:
        task["is_editing"] = False
        queue_manager.clear_edit_state(task["user_id"])
        action_name = "Sample generation" if action == "sample" else "Link generation"
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{msg_id}")]]
        )
        await safe_edit(callback_query.message, 
            f"📝 {action_name} registered. Added to queue (Position: {queue_manager.get_position(task)}).",
            reply_markup=markup,
        )
        input_path = task.get("input_path")
        if input_path and os.path.exists(input_path):
            await queue_manager.continue_task(task)
        return

    if action == "vmerge":
        queue_manager.set_edit_state(task["user_id"], "WAITING_FOR_MERGE_FILES", msg_id)

        # Interactive finish button
        finish_markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✅ Finish & Merge", callback_data=f"edit_finishmerge_{msg_id}"
                    )
                ],
                [InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{msg_id}")],
            ]
        )

        await safe_edit(callback_query.message, 
            "🔗 **Video Merger**\n\n"
            "1. Send the next video you want to merge.\n"
            "2. Repeat for all videos.\n"
            "3. Click **Finish & Merge** below when done.\n\n"
            "*First video is already registered.*",
            reply_markup=finish_markup,
        )
    elif action == "split":
        # Interactive number pad for split parts
        split_markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "2 Parts", callback_data=f"edit_dosplit_2_{msg_id}"
                    ),
                    InlineKeyboardButton(
                        "3 Parts", callback_data=f"edit_dosplit_3_{msg_id}"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "4 Parts", callback_data=f"edit_dosplit_4_{msg_id}"
                    ),
                    InlineKeyboardButton(
                        "5 Parts", callback_data=f"edit_dosplit_5_{msg_id}"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "6 Parts", callback_data=f"edit_dosplit_6_{msg_id}"
                    ),
                    InlineKeyboardButton(
                        "10 Parts", callback_data=f"edit_dosplit_10_{msg_id}"
                    ),
                ],
                [InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{msg_id}")],
            ]
        )

        await safe_edit(callback_query.message, 
            "🔪 **Video Splitter**\n\n"
            "Select how many equal parts you want to split this video into:",
            reply_markup=split_markup,
        )
    elif action == "finishmerge":
        state = queue_manager.get_edit_state(task["user_id"])
        if not state or state["msg_id"] != msg_id:
            await callback_query.answer(
                "❌ Invalid or expired merge session.", show_alert=True
            )
            return

        if len(state["files"]) < 1:
            await callback_query.answer(
                "⚠️ You haven't sent any additional videos to merge.", show_alert=True
            )
            return

        task["merge_files"] = [f["file_id"] for f in state["files"]]
        task["preset_override"] = "edit_vmerge"  # Match media_handler.py
        task["is_editing"] = False
        queue_manager.clear_edit_state(task["user_id"])

        # Proceed to queue
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{msg_id}")]]
        )
        await safe_edit(callback_query.message, 
            f"📝 Merge compilation finished. Added to queue (Position: {queue_manager.get_position(task)}).",
            reply_markup=markup,
        )
        input_path = task.get("input_path")
        if input_path and os.path.exists(input_path):
            await queue_manager.continue_task(task)

    elif action == "dosplit":
        parts = int(data_parts[2])
        task["preset_override"] = f"edit_split_{parts}"  # Match media_handler.py
        task["is_editing"] = False
        queue_manager.clear_edit_state(task["user_id"])

        # Proceed to queue
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{msg_id}")]]
        )
        await safe_edit(callback_query.message, 
            f"📝 Split into {parts} parts registered. Added to queue (Position: {queue_manager.get_position(task)}).",
            reply_markup=markup,
        )
        input_path = task.get("input_path")
        if input_path and os.path.exists(input_path):
            await queue_manager.continue_task(task)

    elif action == "stream":
        input_path = task.get("input_path")
        if input_path and os.path.exists(input_path) and os.path.getsize(input_path) > 0:
            # Download is already finished, show streams immediately
            from bot.services.ffmpeg_service import get_video_info
            
            await safe_edit(callback_query.message, "⏳ Analyzing video streams...", reply_markup=None)
            info = await get_video_info(input_path)
            streams = info.get("streams", []) if info else []
            
            queue_manager.set_edit_state(task["user_id"], "SELECTING_STREAMS", msg_id)
            state = queue_manager.get_edit_state(task["user_id"])
            state["all_streams"] = streams
            state["streams_to_remove"] = set()

            markup = build_stream_keyboard(streams, state["streams_to_remove"], msg_id)
            await safe_edit(callback_query.message, 
                "✂️ **Stream Remover**\n\n"
                "Analysis complete. Select the streams you want to **Remove**:\n"
                "*(Click to toggle, then click Finish)*",
                reply_markup=markup,
            )
        else:
            # We can't show streams until the file is downloaded.
            # Mark the state so the queue knows to pause after download and show the menu.
            task["preset_override"] = "edit_stream_pending"
            queue_manager.set_edit_state(
                task["user_id"], "WAITING_FOR_DOWNLOAD_TO_FINISH", msg_id
            )

            await safe_edit(callback_query.message, 
                "✂️ **Stream Remover**\n\n"
                "Waiting for the video to finish downloading so I can analyze its tracks...\n"
                "The menu will appear automatically.",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{msg_id}")]]
                ),
            )

    elif action == "togglestream":
        idx = int(data_parts[2])
        state = queue_manager.get_edit_state(task["user_id"])
        if not state or state["state"] != "SELECTING_STREAMS":
            await callback_query.answer("❌ Invalid session.", show_alert=True)
            return

        streams_to_remove = state.get("streams_to_remove", set())
        if idx in streams_to_remove:
            streams_to_remove.remove(idx)
        else:
            streams_to_remove.add(idx)
        state["streams_to_remove"] = streams_to_remove

        # Rebuild keyboard
        markup = build_stream_keyboard(state["all_streams"], streams_to_remove, msg_id)
        await callback_query.message.edit_reply_markup(reply_markup=markup)

    elif action == "finishstream":
        state = queue_manager.get_edit_state(task["user_id"])
        if not state or state["state"] != "SELECTING_STREAMS":
            await callback_query.answer("❌ Invalid session.", show_alert=True)
            return

        streams_to_remove = state.get("streams_to_remove", set())
        if not streams_to_remove:
            await callback_query.answer(
                "⚠️ You haven't selected any streams to remove.", show_alert=True
            )
            return

        # Format the preset string to pass multiple indices: e.g., "edit_stream_1_2_5"
        indices_str = "_".join(map(str, streams_to_remove))
        task["preset_override"] = f"edit_stream_{indices_str}"
        task["is_editing"] = False
        import logging

        logging.getLogger(__name__).info(
            f"User finished stream selection for msg {msg_id}. is_editing set to False. Preset: {task['preset_override']}"
        )
        queue_manager.clear_edit_state(task["user_id"])

        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{msg_id}")]]
        )
        await safe_edit(callback_query.message, 
            f"📝 Stream removal registered. Processing...", reply_markup=markup
        )
        await queue_manager.continue_task(task)

    elif action == "avmerge":
        queue_manager.set_edit_state(task["user_id"], "WAITING_FOR_AUDIO_FILE", msg_id)
        await safe_edit(callback_query.message, 
            "🎶 **Audio/Video Merger**\n\n"
            "Send the **Audio File** (MP3, M4A, etc.) you want to merge with this video.\n\n"
            "*Video is already registered.*",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{msg_id}")]]
            ),
        )

    elif action == "rename":
        queue_manager.set_edit_state(task["user_id"], "WAITING_FOR_NEW_NAME", msg_id)
        await safe_edit(callback_query.message, 
            "📝 **Video Renamer**\n\n"
            "This action requires text input.\n"
            "Please **Reply** to this message with the new filename (e.g., `my_vacation.mp4`).",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{msg_id}")]]
            ),
        )

    else:
        await callback_query.answer(
            "⚠️ This feature action is not yet implemented.", show_alert=True
        )
