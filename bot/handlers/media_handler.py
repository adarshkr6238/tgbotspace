import os
import time
import logging
from pyrogram import Client, filters
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from bot.config.config import Config
from bot.utils.progress import progress_bar, format_bytes, is_cancelled, clear_cancel_flag, truncate_text
from bot.services.ffmpeg_service import compress_video, get_video_info, split_video, merge_videos, remove_stream, mux_audio_video, extract_sample
from bot.services.storage_service import setup_storage
from bot.utils.fast_transfer import fast_download, fast_upload

logger = logging.getLogger(__name__)

async def send_log_file(message, text, title="Error Log"):
    log_path = f"error_log_{message.id}.txt"
    try:
        with open(log_path, "w") as f:
            f.write(text)
        await message.reply_document(
            document=log_path,
            caption=f"❌ **{title}**\nFull logs attached above.",
            quote=True
        )
    except Exception as e:
        logger.error(f"Failed to send log file: {e}")
    finally:
        if os.path.exists(log_path):
            os.remove(log_path)

async def handle_video(client, message, queue_manager):
    try:
        user_id = message.from_user.id
        logger.info(f"Received media from user {user_id} (Msg: {message.id})")

        if not message.video and not message.document:
            return

        if message.document:
            mime = message.document.mime_type or ""
            if not mime.startswith("video/"):
                return

        setup_storage()
        status_msg = await message.reply_text("⏳ Analyzing and adding to queue...", quote=True)
        
        duration = message.video.duration if message.video else 0
        if not duration and message.document:
            duration = 0 

        preset_override = None
        caption = message.caption or message.text or ""
        if caption.strip().startswith("/diff"):
            preset_override = "diff"

        task = {
            'message': message,
            'status_msg': status_msg,
            'user_id': user_id,
            'paths': [],
            'input_path': None,
            'duration': duration,
            'is_paused': False,
            'process': None,
            'percentage': 0,
            'preset_override': preset_override
        }
        
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("✨ /diff Quality Mode", callback_data=f"diff_{status_msg.id}")],
            [InlineKeyboardButton("✏️ Edit File", callback_data=f"editmenu_{status_msg.id}")],
            [InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{status_msg.id}")]
        ])
        
        await status_msg.edit_text(
            "⏳ Adding to queue...",
            reply_markup=markup
        )
        
        success, pos = await queue_manager.add_task(task)
        if not success:
            await status_msg.edit_text(f"❌ {pos}")
            return

        await status_msg.edit_text(
            f"📝 Added to queue (Position: {pos})\n\nShort videos (<= 5 min) get priority!",
            reply_markup=markup
        )
    except Exception as e:
        logger.error(f"Error in handle_video for msg {message.id}: {e}", exc_info=True)

async def download_stage(client, task, queue_manager):
    message = task['message']
    status_msg = task['status_msg']
    msg_id = status_msg.id
    
    if is_cancelled(msg_id):
        await status_msg.edit_text("❌ Task Cancelled.")
        return

    markup = InlineKeyboardMarkup([
        [InlineKeyboardButton("✨ /diff Quality Mode", callback_data=f"diff_{msg_id}")],
        [InlineKeyboardButton("✏️ Edit File", callback_data=f"editmenu_{msg_id}")],
        [InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{msg_id}")]
    ])

    await status_msg.edit_text(
        "📥 Downloading...",
        reply_markup=markup
    )
    start_time = time.time()
    last_update = start_time
    
    media = message.video or message.document
    file_ext = os.path.splitext(media.file_name or "video.mp4")[1]
    input_path = os.path.join(Config.DOWNLOAD_DIR, f"{message.id}{file_ext}")
    task['original_name'] = media.file_name or f"video_{message.id}{file_ext}"
    task['paths'].append(input_path)
    task['input_path'] = input_path

    async def down_progress(current, total):
        nonlocal last_update
        last_update = await progress_bar(current, total, "Downloading", status_msg, start_time, last_update, task, reply_markup=markup)

    try:
        setup_storage()
        await fast_download(client, message, input_path, progress=down_progress)
        
        if not task['duration']:
            info = await get_video_info(input_path)
            if info:
                task['duration'] = float(info.get('format', {}).get('duration', 0))

        if is_cancelled(msg_id):
            raise Exception("CANCELLED")
            
        preset_name = task.get('preset_override')
        if preset_name == "edit_stream_pending":
            info = await get_video_info(input_path)
            streams = info.get('streams', []) if info else []
            from bot.handlers.edit_handler import build_stream_keyboard
            
            # Change state to SELECTING_STREAMS
            task['is_editing'] = True
            queue_manager.set_edit_state(task['user_id'], 'SELECTING_STREAMS', msg_id)
            state = queue_manager.get_edit_state(task['user_id'])
            state['all_streams'] = streams
            state['streams_to_remove'] = set()
            
            markup = build_stream_keyboard(streams, state['streams_to_remove'], msg_id)
            await status_msg.edit_text(
                "✂️ **Stream Remover**\n\n"
                "Analysis complete. Select the streams you want to **Remove**:\n"
                "*(Click to toggle, then click Finish)*",
                reply_markup=markup
            )
            # We don't advance to compression stage yet. The queue_manager will pause
            # because 'is_editing' is True.
        elif not task.get('is_editing'):
            await status_msg.edit_text(
                "✅ Ready for processing...",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{msg_id}")]])
            )
    except Exception as e:
        if str(e) == "CANCELLED":
            await status_msg.edit_text("❌ Task Cancelled.")
        else:
            await status_msg.edit_text("❌ **Download Failed.** Sending logs...")
            await send_log_file(message, str(e), "Download Error")
        raise e

async def compression_stage(client, task, queue_manager):
    message = task['message']
    status_msg = task['status_msg']
    user_id = task['user_id']
    input_path = task['input_path']
    msg_id = status_msg.id
    
    preset_name = task.get('preset_override') or queue_manager.get_user_preset(user_id)
    logger.info(f"Starting compression_stage for msg {msg_id}. Preset: {preset_name}")
    
    if is_cancelled(msg_id):
        await status_msg.edit_text("❌ Task Cancelled.")
        return
    
    # Use input extension if it's an edit task, otherwise default to .mp4 for compression
    if preset_name.startswith("edit_"):
        file_ext = os.path.splitext(input_path)[1] or ".mp4"
    else:
        file_ext = ".mp4"
        
    output_path = os.path.join(Config.TEMP_DIR, f"compressed_{message.id}{file_ext}")
    task['paths'].append(output_path)

    markup = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{msg_id}")]])

    await status_msg.edit_text(
        f"⚙️ Processing ({preset_name})...",
        reply_markup=markup
    )
    start_time = time.time()
    last_update = start_time

    async def comp_progress(current, total):
        nonlocal last_update
        if task.get('is_paused'):
            return last_update
        last_update = await progress_bar(current, total, f"Processing ({preset_name})", status_msg, start_time, last_update, task, reply_markup=markup)

    try:
        success = False
        error_msg = ""
        output_files = [output_path] # Default single output
        
        if preset_name.startswith("edit_split_"):
            parts = int(preset_name.split("_")[2])
            out_files, error_msg = await split_video(input_path, Config.TEMP_DIR, parts, comp_progress, task)
            if out_files:
                output_files = out_files
                success = True
        elif preset_name == "edit_sample":
            await status_msg.edit_text("🎬 Extracting sample...", reply_markup=markup)
            success, error_msg = await extract_sample(input_path, output_path, comp_progress, task)
        elif preset_name == "edit_link":
            import uuid
            import shutil
            await status_msg.edit_text("🔗 Generating secure direct link...", reply_markup=markup)
            
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
            return # Exit early, we don't upload back to telegram
        elif preset_name == "edit_vmerge":
            input_paths = [input_path]
            # Download all files registered for merge
            if 'merge_files' in task:
                for idx, file_id in enumerate(task['merge_files']):
                    dl_path = os.path.join(Config.DOWNLOAD_DIR, f"{msg_id}_merge_{idx}.mp4")
                    
                    # Create a progress wrapper for each part
                    part_start_time = time.time()
                    part_last_update = part_start_time
                    async def part_down_progress(current, total):
                        nonlocal part_last_update
                        part_last_update = await progress_bar(
                            current, total, f"Downloading Part {idx+2}", 
                            status_msg, part_start_time, part_last_update, task, reply_markup=markup
                        )
                    
                    # Search for the message containing this file_id to use fast_download
                    # For now, fallback to client.download_media if message not easily accessible
                    # but wrapped in our progress logic
                    await client.download_media(file_id, file_name=dl_path, progress=part_down_progress)
                    
                    input_paths.append(dl_path)
                    task['paths'].append(dl_path)
            
            start_time = time.time()
            last_update = start_time
            await status_msg.edit_text(f"⚙️ Merging {len(input_paths)} videos...", reply_markup=markup)
            success, error_msg = await merge_videos(input_paths, output_path, comp_progress, task)
        elif preset_name.startswith("edit_stream_"):
            if preset_name == "edit_stream_pending":
                # Should not happen here normally due to UI lock, but just in case
                success, error_msg = False, "Waiting for stream selection."
            else:
                indices_str = preset_name.split("edit_stream_")[1]
                indices = [int(x) for x in indices_str.split("_") if x]
                await status_msg.edit_text(f"✂️ Removing {len(indices)} streams...", reply_markup=markup)
                success, error_msg = await remove_stream(input_path, output_path, indices, comp_progress, task)
            
        elif preset_name == "edit_avmerge":
            if 'audio_file' in task:
                audio_path = os.path.join(Config.DOWNLOAD_DIR, f"{msg_id}_audio.m4a")
                
                audio_start_time = time.time()
                audio_last_update = audio_start_time
                async def audio_down_progress(current, total):
                    nonlocal audio_last_update
                    audio_last_update = await progress_bar(
                        current, total, "Downloading Audio", 
                        status_msg, audio_start_time, audio_last_update, task, reply_markup=markup
                    )

                await client.download_media(task['audio_file'], file_name=audio_path, progress=audio_down_progress)
                task['paths'].append(audio_path)
                
                start_time = time.time()
                last_update = start_time
                await status_msg.edit_text("🎶 Merging Audio and Video...", reply_markup=markup)
                success, error_msg = await mux_audio_video(input_path, audio_path, output_path, comp_progress, task)
            else:
                success, error_msg = False, "No audio file provided."
                
        elif preset_name == "edit_rename":
            new_name = task.get('new_name', 'renamed_video.mp4')
            await status_msg.edit_text(f"📝 Renaming to {new_name}...", reply_markup=markup)
            # Rename doesn't need ffmpeg, just copy/move
            import shutil
            shutil.copy2(input_path, output_path)
            # Override output_files to force the new name during upload
            upload_target = os.path.join(Config.TEMP_DIR, new_name)
            os.rename(output_path, upload_target)
            output_files = [upload_target]
            task['paths'].append(upload_target)
            success = True
            error_msg = ""
            
        elif preset_name.startswith("edit_"):
            success = False
            error_msg = "This specific edit feature is still being integrated."
        else:
            success, error_msg = await compress_video(input_path, output_path, preset_name, comp_progress, task)
        
        if is_cancelled(msg_id):
             raise Exception("CANCELLED")
             
        if not success:
            await status_msg.edit_text("❌ **Processing Failed.** Sending logs...")
            await send_log_file(message, error_msg, "Processing Error")
            return

        await status_msg.edit_text(
            "📤 Uploading...",
            reply_markup=markup
        )
        start_time = time.time()
        last_update = start_time

        async def up_progress(current, total):
            nonlocal last_update
            last_update = await progress_bar(current, total, "Uploading", status_msg, start_time, last_update, task, reply_markup=markup)

        orig_size = os.path.getsize(input_path)
        
        # Upload loop for multiple files (like split)
        for i, out_file in enumerate(output_files):
            if not os.path.exists(out_file): continue
            
            comp_size = os.path.getsize(out_file)
            
            if not preset_name.startswith("edit_") and comp_size >= orig_size:
                await status_msg.edit_text("⚠️ Compressed file was larger. Sending original.")
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
                orig_name = task.get('original_name')
                if orig_name:
                    orig_base = os.path.splitext(orig_name)[0]
                    out_ext = os.path.splitext(upload_path)[1]
                    if len(output_files) > 1:
                        show_name = f"{orig_base}_part{i+1}{out_ext}"
                    else:
                        show_name = f"{orig_base}{out_ext}"

            # Use native upload for reliability
            await message.reply_document(
                document=upload_path,
                file_name=show_name,
                caption=caption,
                quote=True,
                progress=up_progress
            )
            
        await status_msg.delete()
        clear_cancel_flag(msg_id)
        import gc
        gc.collect() 
    except Exception as e:
        if str(e) == "CANCELLED":
            await status_msg.edit_text("❌ Task Cancelled.")
        else:
            await status_msg.edit_text("❌ **System Error.** Sending logs...")
            await send_log_file(message, str(e), "System Exception")
        clear_cancel_flag(msg_id)
