from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from bot.handlers.video_edit_handler import handle_remove_streams, handle_extract_streams

class MenuBuilder:
    @staticmethod
    def build_media_menu(msg_id: int, media_type: str) -> InlineKeyboardMarkup:
        buttons = []
        if media_type == 'video':
            # Grouping for video
            buttons = [
                [InlineKeyboardButton("Audio & Subtitles Remover", callback_data=f"vid_rem_asr:{msg_id}"),
                 InlineKeyboardButton("Audio & Subtitles Extractor", callback_data=f"vid_ext_asr:{msg_id}")],
                [InlineKeyboardButton("Caption & Buttons Editor", callback_data=f"vid_edt_cap:{msg_id}")],
                [InlineKeyboardButton("Video Trimmer", callback_data=f"vid_trim:{msg_id}"),
                 InlineKeyboardButton("Video Merger", callback_data=f"vid_merge:{msg_id}")],
                [InlineKeyboardButton("Mute Audio", callback_data=f"vid_mute:{msg_id}")],
                [InlineKeyboardButton("Video And Audio Merger", callback_data=f"vid_mrg_aud:{msg_id}"),
                 InlineKeyboardButton("Video And Subtitle Merger", callback_data=f"vid_mrg_sub:{msg_id}")],
                [InlineKeyboardButton("Video to GIF", callback_data=f"vid_gif:{msg_id}"),
                 InlineKeyboardButton("Video Splitter", callback_data=f"vid_split:{msg_id}")],
                [InlineKeyboardButton("Screenshot Generator", callback_data=f"vid_scr_gen:{msg_id}"),
                 InlineKeyboardButton("Manual Screenshot", callback_data=f"vid_scr_man:{msg_id}")],
                [InlineKeyboardButton("Video Sample", callback_data=f"vid_samp:{msg_id}")],
                [InlineKeyboardButton("Video Converter (Formats)", callback_data=f"vid_conv_f:{msg_id}"),
                 InlineKeyboardButton("Video Optimizer", callback_data=f"vid_opt:{msg_id}")],
                [InlineKeyboardButton("Video Converter (Streams)", callback_data=f"vid_conv_s:{msg_id}")],
                [InlineKeyboardButton("Video Renamer", callback_data=f"vid_ren:{msg_id}"),
                 InlineKeyboardButton("Media Information", callback_data=f"vid_info:{msg_id}")],
                [InlineKeyboardButton("Make Archive", callback_data=f"vid_arch:{msg_id}")]
            ]
        elif media_type == 'audio':
            buttons = [
                [InlineKeyboardButton("Audio Converter", callback_data=f"aud_conv:{msg_id}"),
                 InlineKeyboardButton("Audio Trimmer", callback_data=f"aud_trim:{msg_id}")]
            ]
        elif media_type == 'document':
            buttons = [
                [InlineKeyboardButton("Rename", callback_data=f"doc_ren:{msg_id}")]
            ]
        
        return InlineKeyboardMarkup(buttons)

async def CallbackRouter(client, callback_query, queue_manager):
    data = callback_query.data
    if not data:
        return
    
    parts = data.split(':')
    prefix = parts[0]
    msg_id = int(parts[1]) if len(parts) > 1 else None
    
    task = queue_manager.all_tasks.get(msg_id)
    if not task:
        await callback_query.answer("❌ Task not found.", show_alert=True)
        return

    # Routing logic
    if prefix == "vid_rem_asr":
        from bot.handlers.edit_handler import handle_edit_action
        callback_query.data = f"edit_stream_{msg_id}"
        await handle_edit_action(client, callback_query, queue_manager)
    elif prefix == "vid_ext_asr":
        from bot.handlers.edit_handler import handle_edit_action
        callback_query.data = f"edit_stream_{msg_id}"
        await handle_edit_action(client, callback_query, queue_manager)
    elif prefix == "vid_trim":
        from bot.handlers.video_edit_handler import handle_video_trim
        await handle_video_trim(client, task, queue_manager)
    elif prefix == "vid_merge":
        from bot.handlers.video_edit_handler import handle_video_merge
        await handle_video_merge(client, task, queue_manager)
    elif prefix == "vid_mute":
        from bot.handlers.video_edit_handler import handle_video_mute
        await handle_video_mute(client, task, queue_manager)
    elif prefix.startswith("vid_"):

        from bot.handlers.video_edit_handler import handle_video_merge
        await handle_video_merge(client, task, queue_manager)
    elif prefix == "vid_trim":
        from bot.handlers.video_edit_handler import handle_video_trim
        await handle_video_trim(client, task, queue_manager)
    elif prefix == "vid_merge":
        from bot.handlers.video_edit_handler import handle_video_merge
        await handle_video_merge(client, task, queue_manager)
    elif prefix == "vid_mute":
        from bot.handlers.video_edit_handler import handle_video_mute
        await handle_video_mute(client, task, queue_manager)
    elif prefix.startswith("vid_"):

        print(f"Routing video action: {data}")
    elif prefix.startswith("aud_"):
        print(f"Routing audio action: {data}")
    elif prefix.startswith("doc_"):
        print(f"Routing document action: {data}")
    else:
        print(f"Unknown callback: {data}")
