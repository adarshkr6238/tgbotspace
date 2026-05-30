import logging
import asyncio
import os
import json
import signal
import uvloop
from aiohttp import web
from pyrogram import Client, filters, idle
from bot.config.config import Config
from bot.services.queue_manager import QueueManager
from bot.services.storage_service import setup_storage, cleanup_old_files
from bot.handlers.commands import start_cmd, help_cmd, settings_cmd, set_preset_cb, stats_cmd, queue_cmd, clear_cmd
from bot.handlers.media_handler import handle_video, download_stage, compression_stage
from bot.handlers.edit_handler import handle_edit_menu, handle_edit_action
from bot.utils.progress import cancel_task

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def health_check(request):
    return web.Response(text="Bot Network is running!")

async def start_health_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    
    os.makedirs(Config.PUBLIC_DIR, exist_ok=True)
    app.router.add_static("/dl/", path=Config.PUBLIC_DIR, name="dl")
    
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.environ.get("PORT", 7860))
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Health check server started on port {port}")

class VideoBot(Client):
    def __init__(self, token, session_name):
        logger.info(f"Initializing bot: {session_name}")
        super().__init__(
            session_name,
            api_id=Config.API_ID,
            api_hash=Config.API_HASH,
            bot_token=token,
            workers=32,
            max_concurrent_transmissions=20,
            sleep_threshold=60
        )
        self.queue_manager = QueueManager(self)
        self.queue_manager.process_download = self._download_bridge
        self.queue_manager.process_compression = self._compression_bridge

    async def _download_bridge(self, task):
        await download_stage(self, task, self.queue_manager)

    async def _compression_bridge(self, task):
        await compression_stage(self, task, self.queue_manager)

    async def start(self):
        await super().start()
        self.queue_manager.start_worker()
        logger.info(f"Bot {self.name} started successfully!")

class BotManager:
    def __init__(self):
        self.bots = {} # token -> VideoBot
        self.bots_file = "cloned_bots.json"
        
        # Load saved clones
        self.saved_tokens = []
        if os.path.exists(self.bots_file):
            try:
                with open(self.bots_file, "r") as f:
                    self.saved_tokens = json.load(f)
            except: pass
            
        # Add primary bot token
        if Config.BOT_TOKEN not in self.saved_tokens:
            self.saved_tokens.append(Config.BOT_TOKEN)

    def _save_tokens(self):
        with open(self.bots_file, "w") as f:
            json.dump(self.saved_tokens, f)

    async def start_all(self):
        for i, token in enumerate(self.saved_tokens):
            await self.start_bot(token, f"bot_{i}")
        
        # Global cleanup loop
        asyncio.create_task(self._global_cleanup_loop())

    async def start_bot(self, token, name):
        if token in self.bots:
            return False, "Bot already running."
            
        bot = VideoBot(token, name)
        
        # Wrap handlers to inject the specific bot's queue_manager
        async def _settings_wrapper(c, m): await settings_cmd(c, m, bot.queue_manager)
        async def _settings_cb_wrapper(c, cb): 
            await settings_cmd(c, cb.message, bot.queue_manager)
            await cb.answer()
        async def _set_preset_wrapper(c, cb): await set_preset_cb(c, cb, bot.queue_manager)
        async def _queue_wrapper(c, m): await queue_cmd(c, m, bot.queue_manager)
        async def _clear_wrapper(c, m): await clear_cmd(c, m, bot.queue_manager)
        async def _media_wrapper(c, m):
            state = bot.queue_manager.get_edit_state(m.from_user.id)
            if state:
                if state['state'] == 'WAITING_FOR_MERGE_FILES':
                    media = m.video or m.document
                    if media and (not m.document or (m.document.mime_type and m.document.mime_type.startswith("video/"))):
                        bot.queue_manager.add_edit_file(m.from_user.id, "TBD", media.file_id)

                        from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
                        msg_id = state['msg_id']
                        finish_markup = InlineKeyboardMarkup([
                            [InlineKeyboardButton("✅ Finish & Merge", callback_data=f"edit_finishmerge_{msg_id}")],
                            [InlineKeyboardButton("❌ Cancel", callback_data=f"cancel_{msg_id}")]
                        ])

                        await m.reply_text(
                            f"✅ Video #{len(state['files']) + 1} registered. Send another or click **Finish & Merge**.",
                            reply_markup=finish_markup
                        )
                    else:
                        await m.reply_text("❌ Please send a valid video file.")
                    return
                elif state['state'] == 'WAITING_FOR_AUDIO_FILE':
                    media = m.audio or m.document
                    if media and (not m.document or (m.document.mime_type and m.document.mime_type.startswith("audio/"))):
                        task = bot.queue_manager.all_tasks.get(state['msg_id'])
                        if task:
                            task['audio_file'] = media.file_id
                            task['preset_override'] = "edit_avmerge"
                            bot.queue_manager.clear_edit_state(m.from_user.id)
                            await m.reply_text("✅ Audio received. A/V Merge task added to queue.")
                        else:
                            await m.reply_text("❌ Task expired.")
                            bot.queue_manager.clear_edit_state(m.from_user.id)
                    else:
                        await m.reply_text("❌ Please send a valid audio file.")
                    return
            await handle_video(c, m, bot.queue_manager)

        async def _text_wrapper(c, m):
            state = bot.queue_manager.get_edit_state(m.from_user.id)
            if state and state['state'] == 'WAITING_FOR_NEW_NAME':
                task = bot.queue_manager.all_tasks.get(state['msg_id'])
                if task:
                    new_name = m.text.strip()
                    if not new_name.endswith(".mp4") and not new_name.endswith(".mkv"):
                        new_name += ".mp4"
                    task['new_name'] = new_name
                    task['preset_override'] = "edit_rename"
                    bot.queue_manager.clear_edit_state(m.from_user.id)
                    await m.reply_text(f"✅ Renaming to `{new_name}`. Task added to queue.")
                else:
                    await m.reply_text("❌ Task expired.")
                    bot.queue_manager.clear_edit_state(m.from_user.id)


        
        async def _stats_wrapper(c, m):
            # Also pass bot_manager to stats so owner sees active clones
            await self.enhanced_stats_cmd(c, m, bot.queue_manager)

        async def _cancel_cb_wrapper(c, cb):
            msg_id = int(cb.data.split("_")[1])
            cancel_task(msg_id)
            await cb.answer("Cancelling task...", show_alert=True)
            await cb.message.edit_text("❌ Cancellation requested. Moving to next task...")

        async def _diff_cb_wrapper(c, cb):
            msg_id = int(cb.data.split("_")[1])
            task = bot.queue_manager.all_tasks.get(msg_id)
            if task:
                task['preset_override'] = "diff"
                await cb.answer("✨ Quality Mode (/diff) enabled for this video!", show_alert=True)
            else:
                await cb.answer("❌ Task not found or already processing.", show_alert=True)

        async def _editmenu_cb_wrapper(c, cb):
            await handle_edit_menu(c, cb, bot.queue_manager)

        async def _edit_action_cb_wrapper(c, cb):
            await handle_edit_action(c, cb, bot.queue_manager)

        # Clone Management Commands (Owner Only)
        async def _addbot_cmd(c, m):
            if m.from_user.id != Config.OWNER_ID: return
            parts = m.text.split()
            if len(parts) != 2:
                await m.reply_text("Usage: `/addbot <bot_token>`")
                return
            new_token = parts[1]
            success, msg = await self.add_clone(new_token)
            await m.reply_text(msg)

        async def _delbot_cmd(c, m):
            if m.from_user.id != Config.OWNER_ID: return
            parts = m.text.split()
            if len(parts) != 2:
                await m.reply_text("Usage: `/delbot <bot_token>`")
                return
            target_token = parts[1]
            success, msg = await self.remove_clone(target_token)
            await m.reply_text(msg)

        bot.on_message(filters.command("start") & filters.private)(start_cmd)
        bot.on_message(filters.command("help") & filters.private)(help_cmd)
        bot.on_message(filters.command("settings") & filters.private)(_settings_wrapper)
        bot.on_callback_query(filters.regex("^settings_main$"))(_settings_cb_wrapper)
        bot.on_callback_query(filters.regex("^set_"))(_set_preset_wrapper)
        bot.on_callback_query(filters.regex("^cancel_"))(_cancel_cb_wrapper)
        bot.on_callback_query(filters.regex("^diff_"))(_diff_cb_wrapper)
        bot.on_callback_query(filters.regex("^editmenu_"))(_editmenu_cb_wrapper)
        bot.on_callback_query(filters.regex("^edit_"))(_edit_action_cb_wrapper)
        bot.on_message(filters.command("stats") & filters.private)(_stats_wrapper)
        bot.on_message(filters.command("queue") & filters.private)(_queue_wrapper)
        bot.on_message(filters.command("clear") & filters.private)(_clear_wrapper)
        bot.on_message((filters.video | filters.document) & filters.private)(_media_wrapper)
        bot.on_message(filters.text & filters.private)(_text_wrapper)
        
        # Clone commands
        bot.on_message(filters.command("addbot") & filters.private)(_addbot_cmd)
        bot.on_message(filters.command("delbot") & filters.private)(_delbot_cmd)

        try:
            await bot.start()
            self.bots[token] = bot
            return True, "Bot started successfully."
        except Exception as e:
            logger.error(f"Failed to start bot with token {token[:10]}... : {e}")
            return False, f"Failed to start: {e}"

    async def add_clone(self, token):
        if token in self.saved_tokens:
            return False, "Token is already registered."
        
        name = f"bot_{len(self.saved_tokens)}"
        success, msg = await self.start_bot(token, name)
        if success:
            self.saved_tokens.append(token)
            self._save_tokens()
            return True, f"✅ **Clone Added!** Successfully started and saved new bot."
        return False, msg

    async def remove_clone(self, token):
        if token == Config.BOT_TOKEN:
            return False, "⛔ Cannot delete the primary bot token."
        if token not in self.bots:
            return False, "Bot not found or not running."

        bot = self.bots.pop(token)
        await bot.stop()
        
        if token in self.saved_tokens:
            self.saved_tokens.remove(token)
            self._save_tokens()
            
        # Clean up session file
        session_file = f"{bot.name}.session"
        if os.path.exists(session_file):
            os.remove(session_file)

        return True, "✅ **Clone Removed!** Bot stopped and removed from network."

    async def enhanced_stats_cmd(self, client, message, queue_manager):
        if message.from_user.id != Config.OWNER_ID:
            await message.reply_text("⛔ **Access Denied:** This command is for the owner only.")
            return

        import shutil
        import psutil
        
        total, used, free = shutil.disk_usage("/")
        cpu_percent = psutil.cpu_percent(interval=0.5)
        ram = psutil.virtual_memory()
        
        dl_queue = queue_manager.download_queue.qsize()
        comp_queue = queue_manager.compression_queue.qsize()
        active_dl = queue_manager.active_download_count
        waiting_slot = queue_manager.waiting_for_slot_count
        paused_tasks = len(queue_manager.paused_compression_tasks)
        active_comp = "Yes" if queue_manager.active_compression_task else "No"

        status = (
            "📊 **Network Dashboard**\n\n"
            "💻 **System Health:**\n"
            f"├ **CPU:** {cpu_percent}%\n"
            f"├ **RAM:** {ram.percent}% ({ram.used // (2**20)}MB)\n"
            f"└ **Disk:** {used // (2**20)}MB used\n\n"
            "🤖 **Bot Network:**\n"
            f"└ **Active Clones:** {len(self.bots)}\n\n"
            "⚙️ **This Bot's Pipeline:**\n"
            f"├ **Active Downloads:** {active_dl}/3\n"
            f"├ **Waiting for DL Slot:** {waiting_slot}\n"
            f"├ **Active Compression:** {active_comp}\n"
            f"└ **Paused Compressions:** {paused_tasks}\n\n"
            "📝 **This Bot's Queues:**\n"
            f"├ **Download Queue:** {dl_queue}\n"
            f"└ **Compression Queue:** {comp_queue}"
        )
        await message.reply_text(status)

    async def _global_cleanup_loop(self):
        while True:
            import gc
            cleanup_old_files()
            gc.collect() 
            await asyncio.sleep(600) 

async def main():
    setup_storage()
    asyncio.create_task(start_health_server())
    
    manager = BotManager()
    await manager.start_all()
    
    await idle()

if __name__ == "__main__":
    uvloop.install()
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
    except Exception as e:
        logger.error(f"Fatal error: {e}")
