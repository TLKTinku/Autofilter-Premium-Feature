import logging
from pyrogram import Client, filters
from info import ADMINS

logger = logging.getLogger(__name__)


def _is_admin(uid):
    if not uid:
        return False
    for a in ADMINS or []:
        try:
            if int(a) == int(uid):
                return True
        except (TypeError, ValueError):
            if str(a) == str(uid):
                return True
    return False


@Client.on_message(filters.command(["live_on", "liveon", "live_start"]))
async def live_on(client, message):
    uid = message.from_user.id if message.from_user else 0
    if not _is_admin(uid):
        return await message.reply_text(
            f"Sirf ADMINS.\nTeri ID: <code>{uid}</code>\n"
            f"Render pe ADMINS mein ye ID daal."
        )
    if len(message.command) < 2:
        return await message.reply_text("Usage: <code>/live_on -1001234567890</code>")
    try:
        chat_id = int(message.command[1])
    except ValueError:
        return await message.reply_text("Channel id number hona chahiye, jaise <code>-1001234567890</code>")
    try:
        from userbot_index import enable_live_forward, userbot
        if not userbot or not userbot.is_connected:
            return await message.reply_text("❌ Userbot OFF. USER_SESSION Render pe check karo.")
        cid = await enable_live_forward(chat_id)
        await message.reply_text(f"🟢 Live ON\n<code>{cid}</code>\nBand: <code>/live_off {cid}</code>")
    except Exception as e:
        logger.exception("live_on")
        await message.reply_text(f"❌ Live on fail:\n<code>{e}</code>")


@Client.on_message(filters.command(["live_off", "liveoff", "live_stop"]))
async def live_off(client, message):
    uid = message.from_user.id if message.from_user else 0
    if not _is_admin(uid):
        return await message.reply_text(
            f"Sirf ADMINS.\nTeri ID: <code>{uid}</code>\n"
            f"Render pe ADMINS mein ye ID daal."
        )
    try:
        from userbot_index import disable_live_forward, disable_all_live_forward, INDEXED_CHAT_IDS
        arg = message.command[1] if len(message.command) > 1 else "all"
        if arg.lower() == "all":
            ids = await disable_all_live_forward()
            return await message.reply_text(
                f"🔴 Live OFF (all)\nBand: <code>{', '.join(str(i) for i in ids) or 'none'}</code>"
            )
        chat_id = int(arg)
        await disable_live_forward(chat_id)
        await message.reply_text(
            f"🔴 Live OFF\n<code>{chat_id}</code>\n"
            f"Abhi on: <code>{', '.join(str(c) for c in INDEXED_CHAT_IDS) or 'none'}</code>"
        )
    except Exception as e:
        logger.exception("live_off")
        await message.reply_text(f"❌ Live off fail:\n<code>{e}</code>")


@Client.on_message(filters.command(["live_status", "livestatus"]))
async def live_status(client, message):
    uid = message.from_user.id if message.from_user else 0
    if not _is_admin(uid):
        return await message.reply_text(f"Sirf ADMINS.\nTeri ID: <code>{uid}</code>")
    try:
        from userbot_index import userbot, INDEXED_CHAT_IDS, USERBOT_BACKUP_CHANNEL
        ub = bool(userbot and userbot.is_connected)
        await message.reply_text(
            f"Userbot: <code>{'ON' if ub else 'OFF'}</code>\n"
            f"Backup: <code>{USERBOT_BACKUP_CHANNEL}</code>\n"
            f"Live ON: <code>{', '.join(str(c) for c in INDEXED_CHAT_IDS) or 'none'}</code>\n\n"
            f"<code>/live_on -100ID</code>\n"
            f"<code>/live_off</code>  ya  <code>/live_off -100ID</code>"
        )
    except Exception as e:
        await message.reply_text(f"❌ <code>{e}</code>")
