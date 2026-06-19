import logging
import os
import time

from bot.config.config import Config
from bot.services.ffmpeg_service import (
    compress_video,
    extract_sample,
    get_video_info,
    merge_videos,
    mux_audio_video,
    remove_stream,
    split_video,
)
from bot.services.storage_service import setup_storage
from bot.utils.fast_transfer import fast_download
from bot.utils.progress import (
    safe_edit,
    clear_cancel_flag,
    format_bytes,
    is_cancelled,
    progress_bar,
)
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)


async def send_log_file(message, text, title="Error Log"):
    log_path = f"error_log_{message.id}.txt"
    try:
        with open(log_path, "w") as f:
            f.write(text)
        await message.reply_document(
            document=log_path,
            caption=f"❌ **{title}**\nFull logs attached above.",
            quote=True,
        )
    except Exception as e:
        logger.error(f"Failed to send log file: {e}")
    finally:
        if os.path.exists(log_path):
            os.remove(log_path)


async def handle_video(client, message, queue_manager):
    try:
        # Ignore outgoing messages sent by this bot to avoid infinite loops.
        # Also ensure from_user exists (channels/service messages/self uploads may look different)
        if message.outgoing or not message.from_user:
            return

        user_id = message.from_user.id
        
        # Authorization Check
        is_auth = (
            user_id == Config.OWNER_ID or 
            user_id in Config.AUTH_USERS or 
            message.chat.id == Config.GROUP_ID
        )
        if not is_auth:
            return

        logger.info(f"Received media from user {user_id} in chat {message.chat.id} (Msg: {message.id})")

        if not message.video and not message.document:
            return

        if message.document:
            mime = message.document.mime_type or ""
            if not mime.startswith("video/"):
                return

        setup_storage()

        # DETERMINISTIC LOAD BALANCING
        # Use Message ID modulo number of nodes to decide which node handles this task.
        # This is zero-overhead and prevents duplicate downloads/processing.
        msg_id_val = message.id
        node_name = os.environ.get("SESSION_NAME", "node1")

        # Extract the node number from SESSION_NAME (e.g., "node1" -> 1, "node2" -> 2)
        import re

        node_match = re.search(r"\d+", node_name)
        node_num = int(node_match.group()) if node_match else 1

        # Logic: Node 1 handles odd IDs, Node 2 handles even IDs (or vice versa)
        # (msg_id % 2) is 0 or 1. We map node 1 to 1 and node 2 to 0.
        # Use node_num - 1 to get 0 for node1 and 1 for node2 (more standard)
        is_my_turn = (msg_id_val % 2) == ((node_num - 1) % 2)

        import asyncio
        from pyrogram.errors import FloodWait

        if not is_my_turn:
            logger.info(f"Node {node_name} entering standby for msg {msg_id_val} (Node {(msg_id_val % 2) + 1}'s turn).")
            # Reactive Fallback: Wait to see if the assigned node handles it
            await asyncio.sleep(15)
            
            # Check if any bot in our cluster already replied to this message
            already_claimed = False
            try:
                async for msg in client.get_chat_history(message.chat.id, limit=20):
                    if msg.reply_to_message_id == message.id and msg.from_user and msg.from_user.is_bot:
                        # Someone (likely the other node) is already on it
                        already_claimed = True
                        break
            except Exception as e:
                logger.error(f"Error checking fallback status: {e}")
            
            if already_claimed:
                logger.info(f"Node {node_name} skipping msg {msg_id_val}: already claimed by other node.")
                return
            
            logger.warning(f"Node {node_name} taking over msg {msg_id_val}: assigned node failed to respond in 15s.")

        status_msg = None
        try:
            # Use unique text to avoid MessageNotModified if two edits happen too fast
            status_msg = await message.reply_text(
                f"⏳ Analyzing... (Node {node_num})", quote=True
            )
        except FloodWait as e:
            logger.warning(f"FloodWait on initial reply. Sleeping {e.value}s")
            await asyncio.sleep(e.value)
            status_msg = await message.reply_text(
                f"⏳ Analyzing... (Node {node_num})", quote=True
            )
        except Exception as e:
            logger.error(f"Failed to send initial status msg: {e}")
            return

        duration = message.video.duration if message.video else 0
        if not duration and message.document:
            duration = 0

        preset_override = None
        caption = message.caption or message.text or ""
        if caption.strip().startswith("/diff"):
            preset_override = "diff"

        task = {
            "message": message,
            "status_msg": status_msg,
            "user_id": user_id,
            "paths": [],
            "input_path": None,
            "duration": duration,
            "is_paused": False,
            "process": None,
            "percentage": 0,
            "preset_override": preset_override,
        }

        markup = InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton(
                        "✨ /diff Quality Mode", callback_data=f"diff_{status_msg.id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "✏️ Edit File", callback_data=f"editmenu_{status_msg.id}"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "❌ Cancel", callback_data=f"cancel_{status_msg.id}"
                    )
                ],
            ]
        )

        try:
            await safe_edit(status_msg, "⏳ Adding to queue...", reply_markup=markup)
        except FloodWait as e:
            logger.warning(f"FloodWait on edit. Sleeping {e.value}s")
            await asyncio.sleep(e.value)
            await safe_edit(status_msg, "⏳ Adding to queue...", reply_markup=markup)
        except Exception:
            pass  # Ignore MessageNotModified or similar harmless errors

        success, pos = await queue_manager.add_task(task)
        if not success:
            await safe_edit(status_msg, f"❌ {pos}")
            return

        try:
            await safe_edit(status_msg, 
                f"📝 Added to queue (Position: {pos})\n\nShort videos (<= 5 min) get priority!",
                reply_markup=markup,
            )
        except FloodWait as e:
            await asyncio.sleep(e.value)
            await safe_edit(status_msg, 
                f"📝 Added to queue (Position: {pos})\n\nShort videos (<= 5 min) get priority!",
                reply_markup=markup,
            )
        except Exception:
            pass
    except Exception as e:
        logger.error(f"Error in handle_video for msg {message.id}: {e}", exc_info=True)


async def download_stage(client, task, queue_manager):
    message = task["message"]
    status_msg = task["status_msg"]
    msg_id = status_msg.id

    if is_cancelled(msg_id):
        await safe_edit(status_msg, "❌ Task Cancelled.")
        return

    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "✨ /diff Quality Mode", callback_data=f"diff_{msg_id}"
                )
            ],
            [InlineKeyboardButton("✏️ Edit File", callback_data=f"editmenu_{msg_id}")],
            [InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{msg_id}")],
        ]
    )

    await safe_edit(status_msg, "📥 Downloading...", reply_markup=markup)
    start_time = time.time()
    last_update = start_time

    media = message.video or message.document
    file_ext = os.path.splitext(media.file_name or "video.mp4")[1]
    input_path = os.path.join(Config.DOWNLOAD_DIR, f"{message.id}{file_ext}")
    task["original_name"] = media.file_name or f"video_{message.id}{file_ext}"
    task["paths"].append(input_path)
    task["input_path"] = input_path

    async def down_progress(current, total):
        nonlocal last_update
        last_update = await progress_bar(
            current,
            total,
            "Downloading",
            status_msg,
            start_time,
            last_update,
            task,
            reply_markup=markup,
        )

    try:
        setup_storage()
        await fast_download(client, message, input_path, progress=down_progress)

        # Verify download integrity
        if not os.path.exists(input_path) or os.path.getsize(input_path) == 0:
            raise Exception("Downloaded file is empty or missing.")

        expected_size = media.file_size
        actual_size = os.path.getsize(input_path)
        if abs(actual_size - expected_size) > 1024 * 50:  # Allow 50KB margin
            logger.warning(
                f"File size mismatch for {input_path}: Expected {expected_size}, got {actual_size}"
            )
            # We don't necessarily fail here as Telegram sizes can be slightly off, but it's logged.

        if not task["duration"]:
            info = await get_video_info(input_path)
            if info:
                task["duration"] = float(info.get("format", {}).get("duration", 0))

        if is_cancelled(msg_id):
            raise Exception("CANCELLED")

        preset_name = task.get("preset_override")
        if preset_name == "edit_stream_pending":
            info = await get_video_info(input_path)
            streams = info.get("streams", []) if info else []
            from bot.handlers.edit_handler import build_stream_keyboard

            # Change state to SELECTING_STREAMS
            task["is_editing"] = True
            queue_manager.set_edit_state(task["user_id"], "SELECTING_STREAMS", msg_id)
            state = queue_manager.get_edit_state(task["user_id"])
            state["all_streams"] = streams
            state["streams_to_remove"] = set()

            markup = build_stream_keyboard(streams, state["streams_to_remove"], msg_id)
            await safe_edit(status_msg, 
                "✂️ **Stream Remover**\n\n"
                "Analysis complete. Select the streams you want to **Remove**:\n"
                "*(Click to toggle, then click Finish)*",
                reply_markup=markup,
            )
            # We don't advance to compression stage yet. The queue_manager will pause
            # because 'is_editing' is True.
        elif not task.get("is_editing"):
            await safe_edit(status_msg, 
                "✅ Ready for processing...",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "❌ Cancel", callback_data=f"cancel_{msg_id}"
                            )
                        ]
                    ]
                ),
            )
    except Exception as e:
        if str(e) == "CANCELLED":
            await safe_edit(status_msg, "❌ Task Cancelled.")
        else:
            await safe_edit(status_msg, "❌ **Download Failed.** Sending logs...")
            await send_log_file(message, str(e), "Download Error")
        raise e


async def compression_stage(client, task, queue_manager):
    message = task["message"]
    status_msg = task["status_msg"]
    user_id = task["user_id"]
    input_path = task["input_path"]
    msg_id = status_msg.id

    preset_name = task.get("preset_override") or queue_manager.get_user_preset(user_id)
    logger.info(f"Starting compression_stage for msg {msg_id}. Preset: {preset_name}")

    if is_cancelled(msg_id):
        await safe_edit(status_msg, "❌ Task Cancelled.")
        return

    # Use input extension if it's an edit task, otherwise default to .mp4 for compression
    if preset_name.startswith("edit_"):
        file_ext = os.path.splitext(input_path)[1] or ".mp4"
    else:
        file_ext = ".mp4"

    output_path = os.path.join(Config.TEMP_DIR, f"compressed_{message.id}{file_ext}")
    task["paths"].append(output_path)

    markup = InlineKeyboardMarkup(
        [[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{msg_id}")]]
    )

    await safe_edit(status_msg, f"⚙️ Processing ({preset_name})...", reply_markup=markup)
    start_time = time.time()
    last_update = start_time

    async def comp_progress(current, total):
        nonlocal last_update
        if task.get("is_paused"):
            return last_update
        last_update = await progress_bar(
            current,
            total,
            f"Processing ({preset_name})",
            status_msg,
            start_time,
            last_update,
            task,
            reply_markup=markup,
        )

    try:
        success = False
        error_msg = ""
        output_files = [output_path]  # Default single output

        if preset_name.startswith("edit_split_"):
            parts = int(preset_name.split("_")[2])
            out_files, error_msg = await split_video(
                input_path, Config.TEMP_DIR, parts, comp_progress, task
            )
            if out_files:
                output_files = out_files
                success = True
        elif preset_name == "edit_sample":
            await safe_edit(status_msg, "🎬 Extracting sample...", reply_markup=markup)
            success, error_msg = await extract_sample(
                input_path, output_path, comp_progress, task
            )
        elif preset_name == "edit_link":
            import shutil
            import uuid

            await safe_edit(status_msg, 
                "🔗 Generating secure direct link...", reply_markup=markup
            )

            # Generate secure UUID filename to prevent scraping
            ext = os.path.splitext(input_path)[1]
            secure_filename = f"{uuid.uuid4().hex}{ext}"
            public_path = os.path.join(Config.PUBLIC_DIR, secure_filename)

            shutil.copy2(input_path, public_path)

            # Construct public URL assuming standard HF space format
            # Using environment variables if available, else fallback to shadow62 space
            space_host = os.environ.get("SPACE_HOST", "shadow62-tgbotspace.hf.space")
            download_url = f"https://{space_host}/dl/{secure_filename}"

            caption = (
                f"✅ **File to Link Complete**\n\n"
                f"📥 **Direct Link:** [Click to Download]({download_url})\n\n"
                f"⚠️ *This link has no speed limits but will be permanently deleted after 3 hours for security.*"
            )

            await message.reply_text(caption, quote=True, disable_web_page_preview=True)
            await status_msg.delete()
            clear_cancel_flag(msg_id)
            return  # Exit early, we don't upload back to telegram
        elif preset_name == "edit_vmerge":
            input_paths = [input_path]
            # Download all files registered for merge
            if "merge_files" in task:
                for idx, file_id in enumerate(task["merge_files"]):
                    dl_path = os.path.join(
                        Config.DOWNLOAD_DIR, f"{msg_id}_merge_{idx}.mp4"
                    )

                    # Create a progress wrapper for each part
                    part_start_time = time.time()
                    part_last_update = part_start_time

                    async def part_down_progress(current, total):
                        nonlocal part_last_update
                        part_last_update = await progress_bar(
                            current,
                            total,
                            f"Downloading Part {idx+2}",
                            status_msg,
                            part_start_time,
                            part_last_update,
                            task,
                            reply_markup=markup,
                        )

                    # Search for the message containing this file_id to use fast_download
                    # For now, fallback to client.download_media if message not easily accessible
                    # but wrapped in our progress logic
                    await client.download_media(
                        file_id, file_name=dl_path, progress=part_down_progress
                    )

                    input_paths.append(dl_path)
                    task["paths"].append(dl_path)

            start_time = time.time()
            last_update = start_time
            await safe_edit(status_msg, 
                f"⚙️ Merging {len(input_paths)} videos...", reply_markup=markup
            )
            success, error_msg = await merge_videos(
                input_paths, output_path, comp_progress, task
            )
        elif preset_name.startswith("edit_stream_"):
            if preset_name == "edit_stream_pending":
                # Should not happen here normally due to UI lock, but just in case
                success, error_msg = False, "Waiting for stream selection."
            else:
                indices_str = preset_name.split("edit_stream_")[1]
                indices = [int(x) for x in indices_str.split("_") if x]
                await safe_edit(status_msg, 
                    f"✂️ Removing {len(indices)} streams...", reply_markup=markup
                )
                success, error_msg = await remove_stream(
                    input_path, output_path, indices, comp_progress, task
                )

        elif preset_name == "edit_avmerge":
            if "audio_file" in task:
                audio_path = os.path.join(Config.DOWNLOAD_DIR, f"{msg_id}_audio.m4a")

                audio_start_time = time.time()
                audio_last_update = audio_start_time

                async def audio_down_progress(current, total):
                    nonlocal audio_last_update
                    audio_last_update = await progress_bar(
                        current,
                        total,
                        "Downloading Audio",
                        status_msg,
                        audio_start_time,
                        audio_last_update,
                        task,
                        reply_markup=markup,
                    )

                await client.download_media(
                    task["audio_file"],
                    file_name=audio_path,
                    progress=audio_down_progress,
                )
                task["paths"].append(audio_path)

                start_time = time.time()
                last_update = start_time
                await safe_edit(status_msg, 
                    "🎶 Merging Audio and Video...", reply_markup=markup
                )
                success, error_msg = await mux_audio_video(
                    input_path, audio_path, output_path, comp_progress, task
                )
            else:
                success, error_msg = False, "No audio file provided."

        elif preset_name == "edit_rename":
            new_name = task.get("new_name", "renamed_video.mp4")
            await safe_edit(status_msg, 
                f"📝 Renaming to {new_name}...", reply_markup=markup
            )
            # Rename doesn't need ffmpeg, just copy/move
            import shutil

            shutil.copy2(input_path, output_path)
            # Override output_files to force the new name during upload
            upload_target = os.path.join(Config.TEMP_DIR, new_name)
            os.rename(output_path, upload_target)
            output_files = [upload_target]
            task["paths"].append(upload_target)
            success = True
            error_msg = ""

        elif preset_name.startswith("edit_"):
            success = False
            error_msg = "This specific edit feature is still being integrated."
        else:
            success, error_msg = await compress_video(
                input_path, output_path, preset_name, comp_progress, task
            )

        if is_cancelled(msg_id):
            raise Exception("CANCELLED")

        if not success:
            await safe_edit(status_msg, "❌ **Processing Failed.** Sending logs...")
            await send_log_file(message, error_msg, "Processing Error")
            return

        await safe_edit(status_msg, "📤 Uploading...", reply_markup=markup)
        start_time = time.time()
        last_update = start_time

        async def up_progress(current, total):
            nonlocal last_update
            last_update = await progress_bar(
                current,
                total,
                "Uploading",
                status_msg,
                start_time,
                last_update,
                task,
                reply_markup=markup,
            )

        orig_size = os.path.getsize(input_path)

        # Upload loop for multiple files (like split)
        for i, out_file in enumerate(output_files):
            if not os.path.exists(out_file):
                continue

            comp_size = os.path.getsize(out_file)

            if not preset_name.startswith("edit_") and comp_size >= orig_size:
                await safe_edit(status_msg, 
                    "⚠️ Compressed file was larger. Sending original."
                )
                upload_path = input_path
                final_size = orig_size
                saved_str = "0% (Already optimized)"
            else:
                upload_path = out_file
                final_size = comp_size
                saved = (orig_size - comp_size) / orig_size * 100 if orig_size else 0
                saved_str = f"{saved:.1f}%"

            caption = (
                f"✅ **Processing Complete** {f'({i+1}/{len(output_files)})' if len(output_files) > 1 else ''}\n\n"
                f"📦 **Original:** {format_bytes(orig_size)}\n"
                f"📉 **Final:** {format_bytes(final_size)}\n"
            )
            if not preset_name.startswith("edit_"):
                caption += f"✨ **Saved:** {saved_str}\n"
            caption += f"🛠️ **Preset/Mode:** {preset_name}"

            # Determine the filename to show in Telegram
            show_name = os.path.basename(upload_path)
            if not preset_name == "edit_rename":
                orig_name = task.get("original_name")
                if orig_name:
                    orig_base = os.path.splitext(orig_name)[0]
                    out_ext = os.path.splitext(upload_path)[1]
                    if len(output_files) > 1:
                        show_name = f"{orig_base}_part{i+1}{out_ext}"
                    else:
                        show_name = f"{orig_base}{out_ext}"

            # Robust upload with FloodWait handling
            import asyncio

            from pyrogram.errors import FloodWait

            retries = 3
            while retries > 0:
                try:
                    await message.reply_document(
                        document=upload_path,
                        file_name=show_name,
                        caption=caption,
                        quote=True,
                        progress=up_progress,
                    )
                    break  # Success
                except FloodWait as e:
                    logger.warning(
                        f"FloodWait during upload. Waiting {e.value} seconds..."
                    )
                    await safe_edit(status_msg, 
                        f"⏳ Telegram Rate Limit hit. Waiting {e.value}s before uploading..."
                    )
                    await asyncio.sleep(e.value)
                    retries -= 1
                except Exception as e:
                    logger.error(f"Upload failed: {e}")
                    if retries == 1:
                        raise e
                    await asyncio.sleep(5)
                    retries -= 1

        await status_msg.delete()
        clear_cancel_flag(msg_id)
        import gc

        gc.collect()
    except Exception as e:
        if str(e) == "CANCELLED":
            await safe_edit(status_msg, "❌ Task Cancelled.")
        else:
            await safe_edit(status_msg, "❌ **System Error.** Sending logs...")
            await send_log_file(message, str(e), "System Exception")
        clear_cancel_flag(msg_id)
