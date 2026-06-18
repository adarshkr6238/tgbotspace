from bot.config.config import Config
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message


async def start_cmd(client, message: Message):
    await message.reply_text(
        "👋 **Welcome to Video Compression Bot!**\n\n"
        "Send me any video, and I'll compress it for you.\n"
        "Optimized for High-Performance Hosting.\n\n"
        "Use /help to see compression modes.",
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton("⚙️ Settings", callback_data="settings_main")]]
        ),
    )


async def help_cmd(client, message: Message):
    help_text = (
        "📖 **Help & Info**\n\n"
        "**Compression Modes:**\n"
        "• `low`: Highest quality, largest size\n"
        "• `medium`: Balanced (720p/360p target)\n"
        "• `high`: Smallest size, lower quality (480p/240p target)\n\n"
        "**Supported Formats:** MP4, MKV, MOV, WEBM\n"
        "**Max Size:** No strict limit (MTProto supported).\n"
        "**Queue:** Sequential processing to maintain stability.\n"
        "**Cleanup:** Files deleted immediately after processing."
    )
    await message.reply_text(help_text)


async def settings_cmd(client, message: Message, queue_manager):
    current = queue_manager.get_user_preset(message.from_user.id)
    await message.reply_text(
        f"⚙️ **Settings**\n\nCurrent Preset: **{current}**",
        reply_markup=get_settings_markup(),
    )


def get_settings_markup():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Low Compression", callback_data="set_low")],
            [
                InlineKeyboardButton(
                    "Medium Compression (Default)", callback_data="set_medium"
                )
            ],
            [InlineKeyboardButton("High Compression", callback_data="set_high")],
        ]
    )


async def set_preset_cb(client, callback, queue_manager):
    preset = callback.data.split("_")[1]
    queue_manager.set_user_preset(callback.from_user.id, preset)
    await callback.answer(f"✅ Preset updated to {preset}")
    await callback.edit_message_text(
        f"⚙️ **Settings**\n\nCurrent Preset: **{preset}**",
        reply_markup=get_settings_markup(),
    )


async def stats_cmd(client, message: Message, queue_manager=None):
    if message.from_user.id != Config.OWNER_ID:
        await message.reply_text(
            "⛔ **Access Denied:** This command is for the owner only."
        )
        return

    import shutil

    import aiohttp
    import psutil

    # Node 1 Stats
    total, used, free = shutil.disk_usage("/")
    cpu_percent = psutil.cpu_percent(interval=0.1)
    ram = psutil.virtual_memory()
    node_name = os.environ.get("SESSION_NAME", "node1")

    status = f"📊 **Cluster Dashboard**\n\n📍 **Node: {node_name} (Primary)**\n"
    status += (
        f"├ **CPU:** {cpu_percent}%\n"
        f"├ **RAM:** {ram.percent}% ({ram.used // (2**20)}MB / {ram.total // (2**20)}MB)\n"
        f"└ **Disk:** {used // (2**20)}MB used\n"
    )

    if queue_manager:
        status += (
            f"⚙️ **Processing:** `{queue_manager.get_current_task_info() or 'Idle'}`\n"
            f"📝 **Queue:** {queue_manager.get_queue_status()} tasks\n"
        )

    # Fetch Node 2 Stats
    node2_url = "https://shadow62-tgbotspace2.hf.space"
    if node_name == "node2":  # If running on node 2, try to fetch node 1
        node2_url = "https://shadow62-tgbotspace.hf.space"
        node2_label = "Node 1"
    else:
        node2_label = "Node 2"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(node2_url, timeout=3) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    status += f"\n📍 **{node2_label}**\n"
                    status += (
                        f"├ **CPU:** {data.get('cpu') or 0}%\n"
                        f"├ **RAM:** {data.get('ram_pct') or 0}%\n"
                        f"├ **Disk:** {data.get('disk_used_mb') or 0}MB used\n"
                        f"├ **Processing:** `{data.get('active_comp') or 'Idle'}`\n"
                        f"└ **Queue:** {data.get('dl_queue', 0) + data.get('comp_queue', 0)} tasks\n"
                    )
                else:
                    status += (
                        f"\n📍 **{node2_label}:** ⚠️ Offline (Status {resp.status})\n"
                    )
    except Exception:
        status += f"\n📍 **{node2_label}:** ⚠️ Offline (Unreachable)\n"

    await message.reply_text(status)


async def queue_cmd(client, message: Message, queue_manager):
    count = queue_manager.get_queue_status()
    current = queue_manager.get_current_task_info()

    status = "📝 **Queue Status**\n\n"
    if current:
        status += f"⚙️ **Currently Processing:** `{current}`\n"
    else:
        status += "✅ **Queue is empty.**\n"

    if count > 0:
        status += f"⏳ **Tasks Waiting:** {count}"

    await message.reply_text(status)


async def clear_cmd(client, message: Message, queue_manager):
    if message.from_user.id != Config.OWNER_ID:
        await message.reply_text(
            "⛔ **Access Denied:** This command is for the owner only."
        )
        return

    await message.reply_text(
        "🧹 **Cluster Reset:** Stopping all tasks and wiping storage on both nodes..."
    )

    # Clear local node
    await queue_manager.clear_queues()

    # Clear remote node
    import aiohttp

    node_name = os.environ.get("SESSION_NAME", "node1")
    target_url = (
        "https://shadow62-tgbotspace2.hf.space/clear"
        if node_name != "node2"
        else "https://shadow62-tgbotspace.hf.space/clear"
    )

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(target_url, timeout=5) as resp:
                if resp.status == 200:
                    await message.reply_text(
                        "✅ **Success:** Both nodes wiped and reset."
                    )
                else:
                    await message.reply_text(
                        f"⚠️ **Partial Success:** Local node cleared, but remote node returned status {resp.status}."
                    )
    except Exception as e:
        await message.reply_text(
            f"⚠️ **Partial Success:** Local node cleared, but remote node was unreachable ({e})."
        )
