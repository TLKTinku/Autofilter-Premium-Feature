import asyncio
import logging
import random
import re
from collections import deque
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, UserAlreadyParticipant, InviteHashExpired

from info import API_ID, API_HASH, USER_SESSION, USERBOT_CHANNELS, USERBOT_BACKUP_CHANNEL, MULTIPLE_DB
from database.users_chats_db import db
from database.ia_filterdb import Media, Media2

logger = logging.getLogger(__name__)

userbot = (
    Client("userbot_index_session", api_id=API_ID, api_hash=API_HASH, session_string=USER_SESSION)
    if USER_SESSION else None
)

# Chats the userbot has successfully joined/accessed
INDEXED_CHAT_IDS = set()

# Control flags for active backfills: chat_id -> "running" | "paused" | "stop"
BACKFILL_CONTROL = {}

# Telegram flood/rate-limit protection.  A single queue is shared by live
# indexing and backfill so multiple copy requests cannot hit the account at once.
COPY_LOCK = asyncio.Lock()
LAST_COPY_AT = 0.0
MIN_COPY_INTERVAL = 1.5  # conservative spacing between copy requests
MAX_COPY_RETRIES = 6

async def _safe_copy(message, caption=None, label="copy"):
    """Copy a message with serialization, FloodWait handling and bounded retries.

    This does NOT bypass Telegram limits. It deliberately slows the account when
    Telegram asks us to wait, which prevents one FloodWait from killing live indexing.
    """
    global LAST_COPY_AT
    delay = 3.0
    for attempt in range(1, MAX_COPY_RETRIES + 1):
        try:
            async with COPY_LOCK:
                now = asyncio.get_running_loop().time()
                wait = MIN_COPY_INTERVAL - (now - LAST_COPY_AT)
                if wait > 0:
                    await asyncio.sleep(wait)
                result = await message.copy(USERBOT_BACKUP_CHANNEL, caption=caption)
                LAST_COPY_AT = asyncio.get_running_loop().time()
                return result
        except FloodWait as e:
            wait_for = max(int(getattr(e, "value", 1)), 1)
            logger.warning(f"[USERBOT-{label}] FloodWait: Telegram asked us to wait {wait_for}s (attempt {attempt}/{MAX_COPY_RETRIES})")
            await asyncio.sleep(wait_for + 1)
        except Exception as e:
            if attempt >= MAX_COPY_RETRIES:
                raise
            logger.warning(f"[USERBOT-{label}] Copy failed (attempt {attempt}/{MAX_COPY_RETRIES}): {e}; retrying in {delay:.1f}s")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60.0)


def _clean_caption(text):
    """Strip links, @mentions and t.me references from a caption before re-posting it."""
    if not text:
        return None
    text = str(text)
    text = re.sub(r'(https?://\S+|t\.me/\S+|www\.\S+)', '', text, flags=re.IGNORECASE)
    text = re.sub(r'@\w+', '', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    return text.strip() or None


def _clean_name(file_name):
    """Same normalization the bot's own save_file() uses, so name comparisons match exactly."""
    file_name = re.sub(r"[_\-\.#+$%^&*()!~`,;:\"'?/<>\[\]{}=|\\]", " ", str(file_name))
    return re.sub(r"\s+", " ", file_name).strip()


def _is_short_video(media, min_duration=240):
    """Return True when media duration is known and is shorter than 4 minutes."""
    duration = getattr(media, "duration", None)
    return duration is not None and duration < min_duration


async def _already_have_exact_copy(file_name, file_size):
    """
    TRUE duplicate check: same cleaned name AND same exact file size.
    Different sizes (different quality/print of the same title) are NOT duplicates
    and are allowed through.
    """
    name = _clean_name(file_name)
    query = {"file_name": name, "file_size": file_size}
    try:
        if await Media.count_documents(query, limit=1):
            return True
        if MULTIPLE_DB and await Media2.count_documents(query, limit=1):
            return True
    except Exception as e:
        logger.error(f"[USERBOT] Duplicate-check failed, allowing through: {e}")
    return False


async def _get_progress(chat_id):
    doc = await db.misc.find_one({"_id": f"backfill_{chat_id}"})
    return doc or {"_id": f"backfill_{chat_id}", "last_message_id": 0, "scanned": 0, "forwarded": 0, "skipped": 0, "duplicates": 0, "status": "not_started"}


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


async def _backfill_pass(chat_id, progress):
    """One continuous pass over the channel history, starting from saved progress.
    Raises on unexpected errors so the caller can decide whether to auto-retry."""
    scanned = progress.get("scanned", 0)
    forwarded_count = progress.get("forwarded", 0)
    skipped_count = progress.get("skipped", 0)
    dup_count = progress.get("duplicates", 0)
    offset_id = progress.get("last_message_id", 0)
    last_seen_id = offset_id

    async for message in userbot.get_chat_history(chat_id, offset_id=offset_id):
        while BACKFILL_CONTROL.get(chat_id) == "paused":
            await asyncio.sleep(2)

        if BACKFILL_CONTROL.get(chat_id) == "stop":
            await _save_progress(
                chat_id, last_message_id=last_seen_id, scanned=scanned,
                forwarded=forwarded_count, skipped=skipped_count, duplicates=dup_count, status="stopped"
            )
            logger.info(f"[USERBOT-BACKFILL] Stopped by user at message_id={last_seen_id}.")
            BACKFILL_CONTROL.pop(chat_id, None)
            return scanned, forwarded_count, skipped_count, True  # True = fully stopped by user

        last_seen_id = message.id
        scanned += 1
        media = message.video or message.document

        if media:
            if _is_short_video(media):
                skipped_count += 1
                logger.info(
                    f"[USERBOT-BACKFILL] Skipped short video (<4 min): "
                    f"{getattr(media, 'file_name', '?')} | duration={getattr(media, 'duration', '?')}s"
                )
                await _save_progress(
                    chat_id, last_message_id=last_seen_id, scanned=scanned,
                    forwarded=forwarded_count, skipped=skipped_count,
                    duplicates=dup_count, status="running"
                )
                continue

            is_dup = await _already_have_exact_copy(media.file_name, media.file_size)
            if is_dup:
                dup_count += 1
            else:
                try:
                    await _safe_copy(message, caption=_clean_caption(message.caption), label="BACKFILL")
                    forwarded_count += 1
                except Exception:
                    skipped_count += 1
                    logger.exception(f"[USERBOT-BACKFILL] Failed to forward message {message.id}")

        # Save progress after EVERY message — minimizes duplicate re-processing on any crash/restart
        await _save_progress(
            chat_id, last_message_id=last_seen_id, scanned=scanned,
            forwarded=forwarded_count, skipped=skipped_count, duplicates=dup_count, status="running"
        )
        if scanned % 200 == 0:
            logger.info(
                f"[USERBOT-BACKFILL] message_id={last_seen_id} | scanned={scanned} forwarded={forwarded_count} "
                f"duplicates_skipped={dup_count} failed={skipped_count}"
            )

    await _save_progress(
        chat_id, last_message_id=last_seen_id, scanned=scanned,
        forwarded=forwarded_count, skipped=skipped_count, duplicates=dup_count, status="done"
    )
    return scanned, forwarded_count, skipped_count, False


async def backfill_channel(chat_id, resume=True):
    """
    Runs continuously until the channel is fully scanned OR the user sends /userbot_stop.
    If an unexpected error happens mid-way, it auto-retries on its own (after a short
    pause) instead of dying and waiting for a manual /userbot_resume.
    Progress is saved after every processed message, so even a crash loses at most
    one message of work — no more duplicate re-forwarding on resume.
    """
    if not USERBOT_BACKUP_CHANNEL:
        raise RuntimeError("USERBOT_BACKUP_CHANNEL is not set on Render.")

    BACKFILL_CONTROL[chat_id] = "running"
    retry_delay = 5

    while True:
        progress = await _get_progress(chat_id) if resume else {}
        try:
            scanned, forwarded, skipped, stopped = await _backfill_pass(chat_id, progress)
            retry_delay = 5  # reset backoff after a clean pass
            if stopped:
                return scanned, forwarded, skipped
            logger.info(f"[USERBOT-BACKFILL] DONE. Scanned {scanned}, forwarded {forwarded}, failed {skipped}")
            BACKFILL_CONTROL.pop(chat_id, None)
            return scanned, forwarded, skipped
        except Exception as e:
            if BACKFILL_CONTROL.get(chat_id) == "stop":
                BACKFILL_CONTROL.pop(chat_id, None)
                p = await _get_progress(chat_id)
                return p.get("scanned", 0), p.get("forwarded", 0), p.get("skipped", 0)
            logger.exception(f"[USERBOT-BACKFILL] Pass crashed, auto-retrying in {retry_delay}s: {e}")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 300)  # back off up to 5 min between retries
            resume = True  # always resume from saved progress on auto-retry


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

    # Auto-resume any backfill that was still 'running' when the process last stopped
    # (e.g. due to a Render redeploy/restart) — no manual command needed.
    try:
        async for doc in db.misc.find({"status": "running", "_id": {"$regex": "^backfill_"}}):
            chat_id_str = doc["_id"].replace("backfill_", "")
            try:
                chat_id = int(chat_id_str)
            except ValueError:
                chat_id = chat_id_str
            logger.info(f"[USERBOT] Auto-resuming interrupted backfill for {chat_id}")
            asyncio.create_task(backfill_channel(chat_id, resume=True))
    except Exception as e:
        logger.error(f"[USERBOT] Failed to check for interrupted backfills: {e}")

    @userbot.on_message(filters.channel & (filters.video | filters.document))
    async def _on_new_file(client, message):
        if message.chat.id not in INDEXED_CHAT_IDS:
            return  # ignore channels we weren't asked to index
        if not USERBOT_BACKUP_CHANNEL:
            return
        media = message.video or message.document
        if _is_short_video(media):
            logger.info(
                f"[USERBOT-LIVE] Skipped short video (<4 min): "
                f"{getattr(media, 'file_name', '?')} | duration={getattr(media, 'duration', '?')}s"
            )
            return
        if await _already_have_exact_copy(media.file_name, media.file_size):
            logger.info(f"[USERBOT-LIVE] Skipped exact duplicate: {media.file_name}")
            return
        try:
            await _safe_copy(message, caption=_clean_caption(message.caption), label="LIVE")
            logger.info(f"[USERBOT-LIVE] Copied new file: {getattr(media, 'file_name', '?')}")
        except Exception as e:
            logger.error(f"[USERBOT-LIVE] Failed after retries for message {message.id}: {e}")

    logger.info(f"[USERBOT] Live indexing active for {len(INDEXED_CHAT_IDS)} channel(s), forwarding into {USERBOT_BACKUP_CHANNEL}.")
