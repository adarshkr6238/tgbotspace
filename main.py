import asyncio
import json
import logging
import os

import uvloop
from aiohttp import web
from bot.config.config import Config
from bot.handlers.commands import (
    clear_cmd,
    help_cmd,
    queue_cmd,
    set_preset_cb,
    settings_cmd,
    start_cmd,
)
from bot.handlers.edit_handler import handle_edit_action, handle_edit_menu
from bot.handlers.media_handler import compression_stage, download_stage, handle_video
from bot.services.queue_manager import QueueManager
from bot.services.storage_service import cleanup_old_files, setup_storage
from bot.utils.progress import cancel_task
from pyrogram import Client, filters, idle

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)


async def health_check(request):
    manager = request.app.get("bot_manager")
    stats = {}
    if manager and manager.bots:
        # Assuming only one primary bot for now as per current structure
        bot = list(manager.bots.values())[0]
        qm = bot.queue_manager
        import shutil

        import psutil

        total, used, free = shutil.disk_usage("/")
        ram = psutil.virtual_memory()
        stats = {
            "node": os.environ.get("SESSION_NAME", "unknown"),
            "cpu": psutil.cpu_percent(),
            "ram_pct": ram.percent,
            "disk_used_mb": used // (2**20),
            "dl_queue": qm.download_queue.qsize(),
            "comp_queue": qm.compression_queue.qsize(),
            "active_dl": qm.active_download_count,
            "active_comp": qm.get_current_task_info() or "Idle",
        }
    return web.json_response(stats)


async def remote_clear(request):
    manager = request.app.get("bot_manager")
    if manager and manager.bots:
        for bot in manager.bots.values():
            await bot.queue_manager.clear_queues()
        logger.info("Remote clear triggered and executed.")
        return web.Response(text="OK")
    return web.Response(text="No bots running", status=404)


async def remote_addbot(request):
    manager = request.app.get("bot_manager")
    if manager:
        try:
            data = await request.json()
            token = data.get("token")
            if token:
                success, msg = await manager.add_clone(token, is_remote=True)
                return web.json_response({"success": success, "message": msg})
        except Exception as e:
            return web.json_response({"success": False, "message": str(e)}, status=400)
    return web.json_response({"success": False, "message": "Invalid request"}, status=400)


async def remote_delbot(request):
    manager = request.app.get("bot_manager")
    if manager:
        try:
            data = await request.json()
            token = data.get("token")
            if token:
                success, msg = await manager.remove_clone(token, is_remote=True)
                return web.json_response({"success": success, "message": msg})
        except Exception as e:
            return web.json_response({"success": False, "message": str(e)}, status=400)
    return web.json_response({"success": False, "message": "Invalid request"}, status=400)


async def remote_task_status(request):
    msg_id = int(request.match_info.get("msg_id"))
    manager = request.app.get("bot_manager")
    
    # Check all running bots on this node
    found = False
    for bot in manager.bots.values():
        if msg_id in bot.queue_manager.all_tasks:
            found = True
            break
            
    logger.info(f"Remote task lookup for {msg_id}: {'Found' if found else 'Not Found'} on {os.environ.get('SESSION_NAME', 'node1')}")
    
    if found:
        return web.Response(text="OK")
    return web.Response(status=404)


async def start_health_server(bot_manager):
    app = web.Application()
    app["bot_manager"] = bot_manager
    app.router.add_get("/", health_check)
    app.router.add_get("/tasks/{msg_id}", remote_task_status)
    app.router.add_post("/clear", remote_clear)
    app.router.add_post("/addbot", remote_addbot)
    app.router.add_post("/delbot", remote_delbot)

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
            sleep_threshold=60,
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
        self.bots = {}  # token -> VideoBot
        self.bots_file = "cloned_bots.json"

        # Load saved clones
        self.saved_tokens = []
        if os.path.exists(self.bots_file):
            try:
                with open(self.bots_file, "r") as f:
                    self.saved_tokens = json.load(f)
            except:
                pass

        # Add primary bot token
        if Config.BOT_TOKEN not in self.saved_tokens:
            self.saved_tokens.append(Config.BOT_TOKEN)

    def _save_tokens(self):
        with open(self.bots_file, "w") as f:
            json.dump(self.saved_tokens, f)

    async def start_all(self):
        session_prefix = os.environ.get("SESSION_NAME", "bot")
        for i, token in enumerate(self.saved_tokens):
            await self.start_bot(token, f"{session_prefix}_{i}")

        # Global cleanup loop
        asyncio.create_task(self._global_cleanup_loop())

    async def start_bot(self, token, name):
        if token in self.bots:
            return False, "Bot already running."

        bot = VideoBot(token, name)

        # Wrap handlers to inject the specific bot's queue_manager
        async def _settings_wrapper(c, m):
            await settings_cmd(c, m, bot.queue_manager)

        async def _settings_cb_wrapper(c, cb):
            await settings_cmd(c, cb.message, bot.queue_manager)
            await cb.answer()

        async def _set_preset_wrapper(c, cb):
            await set_preset_cb(c, cb, bot.queue_manager)

        async def _queue_wrapper(c, m):
            await queue_cmd(c, m, bot.queue_manager)

        async def _clear_wrapper(c, m):
            await clear_cmd(c, m, bot.queue_manager)

        async def _media_wrapper(c, m):
            state = bot.queue_manager.get_edit_state(m.from_user.id)
            if state:
                if state["state"] == "WAITING_FOR_MERGE_FILES":
                    media = m.video or m.document
                    if media and (
                        not m.document
                        or (
                            m.document.mime_type
                            and m.document.mime_type.startswith("video/")
                        )
                    ):
                        bot.queue_manager.add_edit_file(
                            m.from_user.id, "TBD", media.file_id
                        )

                        from pyrogram.types import (
                            InlineKeyboardButton,
                            InlineKeyboardMarkup,
                        )

                        msg_id = state["msg_id"]
                        finish_markup = InlineKeyboardMarkup(
                            [
                                [
                                    InlineKeyboardButton(
                                        "✅ Finish & Merge",
                                        callback_data=f"edit_finishmerge_{msg_id}",
                                    )
                                ],
                                [
                                    InlineKeyboardButton(
                                        "❌ Cancel", callback_data=f"cancel_{msg_id}"
                                    )
                                ],
                            ]
                        )

                        await m.reply_text(
                            f"✅ Video #{len(state['files']) + 1} registered. Send another or click **Finish & Merge**.",
                            reply_markup=finish_markup,
                        )
                    else:
                        await m.reply_text("❌ Please send a valid video file.")
                    return
                elif state["state"] == "WAITING_FOR_AUDIO_FILE":
                    media = m.audio or m.document
                    if media and (
                        not m.document
                        or (
                            m.document.mime_type
                            and m.document.mime_type.startswith("audio/")
                        )
                    ):
                        task = bot.queue_manager.all_tasks.get(state["msg_id"])
                        if task:
                            task["audio_file"] = media.file_id
                            task["preset_override"] = "edit_avmerge"
                            bot.queue_manager.clear_edit_state(m.from_user.id)
                            await m.reply_text(
                                "✅ Audio received. A/V Merge task added to queue."
                            )
                            await bot.queue_manager.continue_task(task)
                        else:
                            await m.reply_text("❌ Task expired.")
                            bot.queue_manager.clear_edit_state(m.from_user.id)
                    else:
                        await m.reply_text("❌ Please send a valid audio file.")
                    return
            await handle_video(c, m, bot.queue_manager)

        async def _text_wrapper(c, m):
            state = bot.queue_manager.get_edit_state(m.from_user.id)
            if state and state["state"] == "WAITING_FOR_NEW_NAME":
                task = bot.queue_manager.all_tasks.get(state["msg_id"])
                if task:
                    new_name = m.text.strip()
                    if not new_name.endswith(".mp4") and not new_name.endswith(".mkv"):
                        new_name += ".mp4"
                    task["new_name"] = new_name
                    task["preset_override"] = "edit_rename"
                    bot.queue_manager.clear_edit_state(m.from_user.id)
                    await m.reply_text(
                        f"✅ Renaming to `{new_name}`. Task added to queue."
                    )
                    await bot.queue_manager.continue_task(task)
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
            from bot.utils.progress import safe_edit
            await safe_edit(cb.message, "❌ Cancellation requested. Moving to next task...")

        async def _diff_cb_wrapper(c, cb):
            msg_id = int(cb.data.split("_")[1])
            task = bot.queue_manager.all_tasks.get(msg_id)
            if task:
                task["preset_override"] = "diff"
                await cb.answer(
                    "✨ Quality Mode (/diff) enabled for this video!", show_alert=True
                )
            else:
                await cb.answer(
                    "❌ Task not found or already processing.", show_alert=True
                )

        async def _editmenu_cb_wrapper(c, cb):
            await handle_edit_menu(c, cb, bot.queue_manager)

        async def _edit_action_cb_wrapper(c, cb):
            await handle_edit_action(c, cb, bot.queue_manager)

        async def _compress_cb_wrapper(c, cb):
            msg_id = int(cb.data.split("_")[1])
            task = bot.queue_manager.all_tasks.get(msg_id)
            if not task:
                await cb.answer("❌ Task not found.", show_alert=True)
                return
            
            # Resume task
            task["is_editing"] = False
            await bot.queue_manager.continue_task(task)
            await cb.answer("🗜️ Resuming task...", show_alert=True)
            from bot.utils.progress import safe_edit
            await safe_edit(cb.message, "📝 Task resumed!")

        async def _remstream_cb_wrapper(c, cb):
            msg_id = int(cb.data.split("_")[1])
            # Direct to edit handler for stream removal
            cb.data = f"edit_stream_{msg_id}"
            await handle_edit_action(c, cb, bot.queue_manager)

        async def _link_cb_wrapper(c, cb):
            msg_id = int(cb.data.split("_")[1])
            # Direct to edit handler for link generation
            cb.data = f"edit_link_{msg_id}"
            await handle_edit_action(c, cb, bot.queue_manager)

        # Clone Management Commands (Owner Only)
        async def _addbot_cmd(c, m):
            if m.from_user.id != Config.OWNER_ID:
                return
            parts = m.text.split()
            if len(parts) != 2:
                await m.reply_text("Usage: `/addbot <bot_token>`")
                return
            new_token = parts[1]
            success, msg = await self.add_clone(new_token)
            await m.reply_text(msg)

        async def _delbot_cmd(c, m):
            if m.from_user.id != Config.OWNER_ID:
                return
            parts = m.text.split()
            if len(parts) != 2:
                await m.reply_text("Usage: `/delbot <bot_token>`")
                return
            target_token = parts[1]
            success, msg = await self.remove_clone(target_token)
            await m.reply_text(msg)

        async def _clones_cmd(c, m):
            if m.from_user.id != Config.OWNER_ID:
                return
            
            if len(self.bots) <= 1:
                await m.reply_text("🤖 **Clones Status:**\n\nNo clones currently active on this node.")
                return
                
            status = "🤖 **Active Clones on this Node:**\n\n"
            for tkn, b in self.bots.items():
                if tkn == Config.BOT_TOKEN:
                    status += f"🌟 `Primary Bot` ({b.name})\n"
                else:
                    status += f"🔹 `{tkn[:10]}...` ({b.name})\n"
            await m.reply_text(status)

        bot.on_message(filters.command("start") & (filters.private | filters.chat(Config.GROUP_ID)))(start_cmd)
        bot.on_message(filters.command("help") & (filters.private | filters.chat(Config.GROUP_ID)))(help_cmd)
        bot.on_message(filters.command("settings") & (filters.private | filters.chat(Config.GROUP_ID)))(_settings_wrapper)
        bot.on_message(filters.command("stats") & (filters.private | filters.chat(Config.GROUP_ID)))(_stats_wrapper)
        bot.on_message(filters.command("queue") & (filters.private | filters.chat(Config.GROUP_ID)))(_queue_wrapper)
        bot.on_message(filters.command("clear") & (filters.private | filters.chat(Config.GROUP_ID)))(_clear_wrapper)

        # Clone commands MUST be registered before generic text handler
        bot.on_message(filters.command("addbot") & (filters.private | filters.chat(Config.GROUP_ID)))(_addbot_cmd)
        bot.on_message(filters.command("delbot") & (filters.private | filters.chat(Config.GROUP_ID)))(_delbot_cmd)
        bot.on_message(filters.command("clones") & (filters.private | filters.chat(Config.GROUP_ID)))(_clones_cmd)

        from bot.utils.menu_builder import CallbackRouter

        async def _universal_cb_wrapper(c, cb):
            CallbackRouter(cb)
            
        bot.on_callback_query(filters.regex("^vid_"))(_universal_cb_wrapper)
        bot.on_callback_query(filters.regex("^aud_"))(_universal_cb_wrapper)
        bot.on_callback_query(filters.regex("^doc_"))(_universal_cb_wrapper)
        bot.on_callback_query(filters.regex("^cancel_"))(_cancel_cb_wrapper)
        
        bot.on_message((filters.video | filters.document) & (filters.private | filters.chat(Config.GROUP_ID)) & filters.incoming)(
            _media_wrapper
        )
        bot.on_message(filters.text & (filters.private | filters.chat(Config.GROUP_ID)))(_text_wrapper)

        from pyrogram.errors import FloodWait
        retries = 3
        while retries > 0:
            try:
                await bot.start()
                self.bots[token] = bot
                return True, "Bot started successfully."
            except FloodWait as e:
                logger.warning(f"FloodWait during auth. Sleeping {e.value} seconds... (Retries left: {retries-1})")
                await asyncio.sleep(e.value)
                retries -= 1
            except Exception as e:
                logger.error(f"Failed to start bot with token {token[:10]}... : {e}")
                return False, f"Failed to start: {e}"
        
        return False, "Failed to start due to persistent rate limit."

    async def add_clone(self, token, is_remote=False):
        if token in self.saved_tokens:
            return False, "Token is already registered."

        name = f"{os.environ.get('SESSION_NAME', 'bot')}_clone_{len(self.saved_tokens)}"
        success, msg = await self.start_bot(token, name)
        if success:
            self.saved_tokens.append(token)
            self._save_tokens()
            
            res_msg = "✅ **Clone Added!** Successfully started and saved new bot."
            if not is_remote:
                import aiohttp
                node_name = os.environ.get("SESSION_NAME", "node1")
                target_url = (
                    "https://shadow62-tgbotspace2.hf.space/addbot"
                    if node_name != "node2"
                    else "https://shadow62-tgbotspace.hf.space/addbot"
                )
                try:
                    async with aiohttp.ClientSession() as session:
                        async with session.post(target_url, json={"token": token}, timeout=5) as resp:
                            if resp.status == 200:
                                res_msg += "\n🌐 **Cluster Sync:** Remote node synced successfully."
                            else:
                                res_msg += f"\n⚠️ **Cluster Sync:** Remote node returned status {resp.status}."
                except Exception as e:
                    res_msg += f"\n⚠️ **Cluster Sync:** Remote node unreachable ({e})."
            return True, res_msg
        return False, f"❌ **Failed to add clone:**\n`{msg}`\n*(If this is a FloodWait, try again later.)*"

    async def remove_clone(self, token, is_remote=False):
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

        res_msg = "✅ **Clone Removed!** Bot stopped and removed from network."
        if not is_remote:
            import aiohttp
            node_name = os.environ.get("SESSION_NAME", "node1")
            target_url = (
                "https://shadow62-tgbotspace2.hf.space/delbot"
                if node_name != "node2"
                else "https://shadow62-tgbotspace.hf.space/delbot"
            )
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(target_url, json={"token": token}, timeout=5) as resp:
                        if resp.status == 200:
                            res_msg += "\n🌐 **Cluster Sync:** Remote node synced successfully."
                        else:
                            res_msg += f"\n⚠️ **Cluster Sync:** Remote node returned status {resp.status}."
            except Exception as e:
                res_msg += f"\n⚠️ **Cluster Sync:** Remote node unreachable ({e})."
        return True, res_msg

    async def enhanced_stats_cmd(self, client, message, queue_manager):
        if message.from_user.id != Config.OWNER_ID:
            await message.reply_text(
                "⛔ **Access Denied:** This command is for the owner only."
            )
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

    async def _cluster_health_loop(self):
        import aiohttp
        node_name = os.environ.get("SESSION_NAME", "node1")
        target_url = (
            "https://shadow62-tgbotspace2.hf.space/"
            if node_name != "node2"
            else "https://shadow62-tgbotspace.hf.space/"
        )
        failures = 0
        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(target_url, timeout=10) as resp:
                        if resp.status == 200:
                            os.environ["OTHER_NODE_ALIVE"] = "1"
                            failures = 0
                        else:
                            failures += 1
            except Exception:
                failures += 1
            
            if failures >= 3:
                os.environ["OTHER_NODE_ALIVE"] = "0"
            
            await asyncio.sleep(20)

    async def _global_cleanup_loop(self):
        while True:
            import gc

            cleanup_old_files()
            gc.collect()
            await asyncio.sleep(600)


async def main():
    # Enforce cluster synchronization via SPACE_ID auto-detection
    space_id = os.environ.get("SPACE_ID", "")
    if space_id:
        space_name = space_id.split("/")[-1].lower()
        if "2" in space_name or "tgbotspace2" in space_name:
            os.environ["SESSION_NAME"] = "node2"
        else:
            os.environ["SESSION_NAME"] = "node1"
    
    if not os.environ.get("SESSION_NAME"):
        os.environ["SESSION_NAME"] = "node1"

    logger.info(f"🚀 Starting Cluster Node: {os.environ['SESSION_NAME']}")
    
    setup_storage()
    manager = BotManager()
    await start_health_server(manager)
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
