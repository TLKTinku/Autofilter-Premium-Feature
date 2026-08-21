import asyncio
import logging
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, UserAlreadyParticipant, InviteHashExpired

from info import API_ID, API_HASH, USER_SESSION, USERBOT_CHANNELS, ADMINS
from database.ia_filterdb import save_file
from database.users_chats_db import db

logger = logging.getLogger(__name__)

userbot = (
    Client("userbot_index_session", api_id=API_ID, api_hash=API_HASH, session_string=USER_SESSION)
    if USER_SESSION else None
)

# Chats the userbot has successfully joined/accessed, so live indexing knows which ones are "ours"
INDEXED_CHAT_IDS = set()


async def _get_progress(chat_id):
    doc = await db.misc.find_one({"_id": f"backfill_{chat_id}"})
    return doc or {"_id": f"backfill_{chat_id}", "last_message_id": 0, "scanned": 0, "saved": 0, "skipped": 0, "status": "not_started"}


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
    Full-history scan of a channel, indexing every video/document found.
    Progress (current message id, counts) is saved to the DB every 500 messages,
    so if the process is interrupted it can RESUME from where it left off
    instead of starting over.
    """
    progress = await _get_progress(chat_id) if resume else {"last_message_id": 0, "scanned": 0, "saved": 0, "skipped": 0}
    scanned = progress.get("scanned", 0)
    saved_count = progress.get("saved", 0)
    skipped_count = progress.get("skipped", 0)
    offset_id = progress.get("last_message_id", 0)

    await _save_progress(chat_id, status="running")
    last_seen_id = offset_id

    async for message in userbot.get_chat_history(chat_id, offset_id=offset_id):
        last_seen_id = message.id
        scanned += 1
        media = message.video or message.document
        if media:
            media.caption = message.caption
            try:
                saved, _ = await save_file(media)
                if saved:
                    saved_count += 1
                else:
                    skipped_count += 1
            except FloodWait as e:
                await asyncio.sleep(e.value)
            except Exception as e:
                logger.error(f"[USERBOT-BACKFILL] Failed on '{getattr(media, 'file_name', '?')}': {e}")
        if scanned % 500 == 0:
            logger.info(
                f"[USERBOT-BACKFILL] Now at message_id={last_seen_id} | scanned={scanned} saved={saved_count} skipped={skipped_count}"
            )
            await _save_progress(
                chat_id, last_message_id=last_seen_id, scanned=scanned,
                saved=saved_count, skipped=skipped_count, status="running"
            )

    await _save_progress(
        chat_id, last_message_id=last_seen_id, scanned=scanned,
        saved=saved_count, skipped=skipped_count, status="done"
    )
    logger.info(f"[USERBOT-BACKFILL] DONE. Scanned {scanned}, saved {saved_count}, skipped {skipped_count}")
    return scanned, saved_count, skipped_count


async def start_userbot():
    if not userbot:
        logger.info("USER_SESSION not set — userbot indexer is disabled.")
        return

    await userbot.start()
    me = await userbot.get_me()
    logger.info(f"[USERBOT] Logged in as {me.first_name} ({me.id})")

    for target in USERBOT_CHANNELS:
        await _join_target(target)

    @userbot.on_message(filters.channel & (filters.video | filters.document))
    async def _on_new_file(client, message):
        if message.chat.id not in INDEXED_CHAT_IDS:
            return  # ignore channels we weren't asked to index
        media = message.video or message.document
        if not media:
            return
        media.caption = message.caption
        try:
            saved, _ = await save_file(media)
            if saved:
                logger.info(f"[USERBOT-LIVE] Indexed new file: {media.file_name}")
        except Exception as e:
            logger.error(f"[USERBOT-LIVE] Failed to index '{getattr(media, 'file_name', '?')}': {e}")

    logger.info(f"[USERBOT] Live indexing active for {len(INDEXED_CHAT_IDS)} channel(s).")
