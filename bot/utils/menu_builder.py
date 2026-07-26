from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton

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

def CallbackRouter(callback_query):
    data = callback_query.data
    if not data:
        return
    
    prefix = data.split(':')[0]
    
    # Simple routing logic
    if prefix.startswith("vid_"):
        # Route to video handlers
        print(f"Routing video action: {data}")
    elif prefix.startswith("aud_"):
        # Route to audio handlers
        print(f"Routing audio action: {data}")
    elif prefix.startswith("doc_"):
        # Route to document handlers
        print(f"Routing document action: {data}")
    else:
        print(f"Unknown callback prefix: {prefix}")
