# Temporary diagnostic plugin - delete this file after the bot is fixed.
import logging
from pyrogram import Client, filters

logger = logging.getLogger(__name__)
print("=== DEBUG BUILD v1 LOADED (zz_debug.py) ===", flush=True)


# Runs BEFORE every other handler (group -10) and only writes to Render logs.
@Client.on_message(filters.incoming, group=-10)
async def _log_every_update(client, message):
    try:
        who = message.from_user.id if message.from_user else None
        print(f"=== UPDATE RECEIVED: chat={message.chat.id} user={who} text={message.text!r}", flush=True)
    except Exception as e:
        print(f"=== UPDATE LOG ERROR: {e}", flush=True)


@Client.on_message(filters.command("ping"), group=-9)
async def _ping(client, message):
    await message.reply_text("pong 🟢 bot is alive and receiving messages")


# Sends a plain "alive" line on /start, before the normal /start handler runs.
@Client.on_message(filters.command("start") & filters.private, group=-9)
async def _start_alive(client, message):
    try:
        await message.reply_text("🟢 Bot online. (debug)")
    except Exception as e:
        print(f"=== DEBUG START REPLY FAILED: {e}", flush=True)
