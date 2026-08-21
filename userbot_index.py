import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, UserAlreadyParticipant, InviteHashExpired

from info import API_ID, API_HASH, USER_SESSION, USERBOT_CHANNELS, USERBOT_BACKUP_CHANNEL
from database.users_chats_db import db

logger = logging.getLogger(__name__)

userbot = (
    Client("userbot_index_session", api_id=API_ID, api_hash=API_HASH, session_string=USER_SESSION)
    if USER_SESSION else None
)

# Chats the userbot has successfully joined/accessed
INDEXED_CHAT_IDS = set()

# Control flags for active backfills: chat_id -> "running" | "paused" | "stop"
BACKFILL_CONTROL = {}


async def _get_progress(chat_id):
    doc = await db.misc.find_one({"_id": f"backfill_{chat_id}"})
    return doc or {"_id": f"backfill_{chat_id}", "last_message_id": 0, "scanned": 0, "forwarded": 0, "skipped": 0, "status": "not_started"}


async def _save_progress(chat_id, **fields):
    await db.misc.update_one({"_id": f"backfill_{chat_id}"}, {"$set": fields}, upsert=True)


async def _join_target(target: str):
    """Join a channel by invite link, or just resolve it if given as an ID/username."""
    try:
        if target.startswith("http") or target.startswith("+") or "joinchat" in target:
            try:
                chat = await userbot.join_chat(target)
            except UserAlreadyParticipant:
                chat = await userbot.get_chat(target)
        else:
            chat_ref = int(target) if target.lstrip("-").isdigit() else target
            chat = await userbot.get_chat(chat_ref)
        INDEXED_CHAT_IDS.add(chat.id)
        logger.info(f"[USERBOT] Ready on channel: {chat.title} ({chat.id})")
        return chat
    except InviteHashExpired:
        logger.error(f"[USERBOT] Invite link expired/invalid: {target}")
    except Exception as e:
        logger.error(f"[USERBOT] Could not join/access {target}: {e}")
    return None


async def backfill_channel(chat_id, resume=True):
    """
    Full-history scan of a channel. Every video/document found gets FORWARDED
    (server-side copy, no download) into YOUR OWN backup channel
    (USERBOT_BACKUP_CHANNEL). The bot's existing live-index watches that
    backup channel and saves everything automatically — no direct DB writes here.
    Progress is saved so this can be safely resumed if interrupted.
    """
    if not USERBOT_BACKUP_CHANNEL:
        raise RuntimeError("USERBOT_BACKUP_CHANNEL is not set on Render.")

    progress = await _get_progress(chat_id) if resume else {"last_message_id": 0, "scanned": 0, "forwarded": 0, "skipped": 0}
    scanned = progress.get("scanned", 0)
    forwarded_count = progress.get("forwarded", 0)
    skipped_count = progress.get("skipped", 0)
    offset_id = progress.get("last_message_id", 0)

    await _save_progress(chat_id, status="running")
    BACKFILL_CONTROL[chat_id] = "running"
    last_seen_id = offset_id

    async for message in userbot.get_chat_history(chat_id, offset_id=offset_id):
        # Handle pause: wait here (without losing our place) until resumed or stopped
        while BACKFILL_CONTROL.get(chat_id) == "paused":
            await asyncio.sleep(2)

        # Handle stop: save progress at current point and exit cleanly
        if BACKFILL_CONTROL.get(chat_id) == "stop":
            await _save_progress(
                chat_id, last_message_id=last_seen_id, scanned=scanned,
                forwarded=forwarded_count, skipped=skipped_count, status="stopped"
            )
            logger.info(f"[USERBOT-BACKFILL] Stopped by user at message_id={last_seen_id}.")
            BACKFILL_CONTROL.pop(chat_id, None)
            return scanned, forwarded_count, skipped_count

        last_seen_id = message.id
        scanned += 1
        media = message.video or message.document
        if media:
            try:
                await message.forward(USERBOT_BACKUP_CHANNEL)
                forwarded_count += 1
                await asyncio.sleep(1.2)  # gentle pacing to avoid flood limits
            except FloodWait as e:
                logger.warning(f"[USERBOT-BACKFILL] FloodWait {e.value}s at message {message.id}")
                await asyncio.sleep(e.value)
            except Exception as e:
                skipped_count += 1
                logger.exception(f"[USERBOT-BACKFILL] Failed to forward message {message.id}")
        if scanned % 200 == 0:
            logger.info(
                f"[USERBOT-BACKFILL] Now at message_id={last_seen_id} | scanned={scanned} forwarded={forwarded_count} skipped={skipped_count}"
            )
            await _save_progress(
                chat_id, last_message_id=last_seen_id, scanned=scanned,
                forwarded=forwarded_count, skipped=skipped_count, status="running"
            )

    await _save_progress(
        chat_id, last_message_id=last_seen_id, scanned=scanned,
        forwarded=forwarded_count, skipped=skipped_count, status="done"
    )
    BACKFILL_CONTROL.pop(chat_id, None)
    logger.info(f"[USERBOT-BACKFILL] DONE. Scanned {scanned}, forwarded {forwarded_count}, skipped {skipped_count}")
    return scanned, forwarded_count, skipped_count


async def start_userbot():
    if not userbot:
        logger.info("USER_SESSION not set — userbot indexer is disabled.")
        return
    if not USERBOT_BACKUP_CHANNEL:
        logger.warning("USERBOT_BACKUP_CHANNEL not set — userbot will join channels but can't forward files anywhere yet.")

    await userbot.start()
    me = await userbot.get_me()
    logger.info(f"[USERBOT] Logged in as {me.first_name} ({me.id})")

    for target in USERBOT_CHANNELS:
        await _join_target(target)

    @userbot.on_message(filters.channel & (filters.video | filters.document))
    async def _on_new_file(client, message):
        if message.chat.id not in INDEXED_CHAT_IDS:
            return  # ignore channels we weren't asked to index
        if not USERBOT_BACKUP_CHANNEL:
            return
        try:
            await message.forward(USERBOT_BACKUP_CHANNEL)
            logger.info(f"[USERBOT-LIVE] Forwarded new file: {getattr(message.video or message.document, 'file_name', '?')}")
        except Exception as e:
            logger.error(f"[USERBOT-LIVE] Failed to forward message {message.id}: {e}")

    logger.info(f"[USERBOT] Live indexing active for {len(INDEXED_CHAT_IDS)} channel(s), forwarding into {USERBOT_BACKUP_CHANNEL}.")
