import asyncio
import logging
import random
import re
import hashlib
from collections import deque
from pyrogram import Client, filters
from pyrogram.errors import FloodWait, UserAlreadyParticipant, InviteHashExpired
from pymongo.errors import DuplicateKeyError

from info import API_ID, API_HASH, USER_SESSION, USERBOT_CHANNELS, USERBOT_BACKUP_CHANNEL, MULTIPLE_DB
from database.users_chats_db import db
from database.ia_filterdb import Media, Media2, unpack_new_file_id, is_useless_filename, clean_media_filename

logger = logging.getLogger(__name__)

userbot = (
    Client("userbot_index_session", api_id=API_ID, api_hash=API_HASH, session_string=USER_SESSION)
    if USER_SESSION else None
)

# Chats the userbot has successfully joined/accessed
INDEXED_CHAT_IDS = set()

# Control flags for active backfills: chat_id -> "running" | "paused" | "stop"
BACKFILL_CONTROL = {}
# Immediate jump: chat_id -> message_id. Backfill checks this every message.
SKIP_TO = {}
REPAIR_STATUS = {
    "state": "idle",
    "step": "",
    "chat_id": None,
    "scanned": 0,
    "fixed": 0,
    "skipped": 0,
    "error": None,
}
JOBS = {
    "repairnames": {"state": "idle"},
    "strip_captions": {"state": "idle"},
}

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
    """Strip every link and @username, keep only the title text."""
    if not text:
        return None
    text = str(text)
    text = re.sub(r'<a\s[^>]*>.*?</a>', ' ', text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r'<[^>]+>', ' ', text)
    text = re.sub(
        r'(?:https?://|www\.)\S+|(?:t\.me|telegram\.(?:me|dog)|tg://)\S*',
        ' ',
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(r'(?i)@?seeai_bbot\b|@?seeai\b', '', text)
    # @user or @user_name (1 underscore max) poora hatao
    text = re.sub(r'@([A-Za-z][A-Za-z0-9]{2,31}(?:_[A-Za-z0-9]{2,31})?)\b', '', text)
    text = re.sub(r'(?m)(^|[\s\[\(\-])@([A-Za-z][A-Za-z0-9]{2,31})_', r'\1', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    cleaned = text.strip() or None
    if cleaned and re.fullmatch(r'\.(mkv|mp4|avi|mov|webm|m4v|ts)', cleaned, re.I):
        return None
    return cleaned


def _clean_name(file_name):
    """Same normalization the bot's own save_file() uses, so name comparisons match exactly."""
    file_name = re.sub(r"[_\-\.#+$%^&*()!~`,;:\"'?/<>\[\]{}=|\\]", " ", str(file_name))
    return re.sub(r"\s+", " ", file_name).strip()


def _is_short_video(media, min_duration=240):
    """Return True when media duration is known and is shorter than 4 minutes."""
    duration = getattr(media, "duration", None)
    return duration is not None and duration < min_duration


_seen_collection = db.db.userbot_seen_hashes  # compact version — stores only a short hash per file, not full names


def _seen_key(file_name, file_size):
    """A short, fixed-size fingerprint for (cleaned name, size) — takes far less
    space than storing the full file name in every tracking document."""
    name = _clean_name(file_name)
    raw = f"{name}|{file_size}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


async def _already_have_exact_copy(file_name, file_size):
    """
    TRUE, INSTANT duplicate check: same cleaned name AND same exact file size.
    This does NOT rely on the bot's own MongoDB save (which happens later,
    asynchronously, after the file lands in the backup channel and the bot's
    separate live-index picks it up — that has lag, and checking against it
    directly caused a race condition where several duplicates got forwarded
    before the first one was even saved).

    Instead, we atomically CLAIM a short hash of (name, size) ourselves the
    moment we decide to forward it — using the hash as the document's own
    _id (which is unique and indexed by default, no extra index needed).
    If the claim succeeds, we've never seen it before — proceed.
    If it fails (duplicate _id), someone already claimed it — skip.
    This gives the exact same protection as before, but each tracking
    document is now just a tiny hash instead of a full file name.
    """
    key = _seen_key(file_name, file_size)

    # Fast path: hash tracker is a unique _id lookup.
    try:
        if await _seen_collection.find_one({"_id": key}, {"_id": 1}):
            return True
    except Exception:
        pass

    name = _clean_name(file_name)
    query = {"file_name": name, "file_size": file_size}
    try:
        if await Media.collection.find_one(query, {"_id": 1}):
            await _seen_collection.update_one({"_id": key}, {"$set": {"via": "media"}}, upsert=True)
            return True
        if MULTIPLE_DB and await Media2.collection.find_one(query, {"_id": 1}):
            await _seen_collection.update_one({"_id": key}, {"$set": {"via": "media2"}}, upsert=True)
            return True
    except Exception as e:
        logger.error(f"[USERBOT] Duplicate-check against main DB failed (continuing anyway): {e}")

    try:
        await _seen_collection.insert_one({"_id": key})
    except DuplicateKeyError:
        return True
    except Exception:
        return False
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
        try:
            await db.misc.update_one(
                {"_id": "userbot_live_chats"},
                {"$addToSet": {"chat_ids": chat.id}},
                upsert=True,
            )
        except Exception:
            pass
        logger.info(f"[USERBOT] Ready on channel: {chat.title} ({chat.id})")
        return chat
    except InviteHashExpired:
        logger.error(f"[USERBOT] Invite link expired/invalid: {target}")
    except Exception as e:
        logger.error(f"[USERBOT] Could not join/access {target}: {e}")
    return None


async def _is_live_disabled(chat_id):
    try:
        doc = await db.misc.find_one({"_id": "userbot_live_disabled"})
        blocked = set((doc or {}).get("chat_ids") or [])
        return chat_id in blocked or str(chat_id) in blocked
    except Exception:
        return False


async def enable_live_forward(chat_id):
    chat_id = int(chat_id)
    await db.misc.update_one(
        {"_id": "userbot_live_disabled"},
        {"$pull": {"chat_ids": {"$in": [chat_id, str(chat_id)]}}},
        upsert=True,
    )
    chat = await _join_target(str(chat_id))
    if not chat:
        raise RuntimeError("Channel join/access fail — userbot us channel pe nahi ja saka")
    return chat.id


async def disable_live_forward(chat_id):
    chat_id = int(chat_id)
    INDEXED_CHAT_IDS.discard(chat_id)
    await db.misc.update_one(
        {"_id": "userbot_live_chats"},
        {"$pull": {"chat_ids": {"$in": [chat_id, str(chat_id)]}}},
        upsert=True,
    )
    await db.misc.update_one(
        {"_id": "userbot_live_disabled"},
        {"$addToSet": {"chat_ids": chat_id}},
        upsert=True,
    )


async def disable_all_live_forward():
    ids = list(INDEXED_CHAT_IDS)
    INDEXED_CHAT_IDS.clear()
    await db.misc.update_one(
        {"_id": "userbot_live_chats"},
        {"$set": {"chat_ids": []}},
        upsert=True,
    )
    if ids:
        await db.misc.update_one(
            {"_id": "userbot_live_disabled"},
            {"$addToSet": {"chat_ids": {"$each": ids}}},
            upsert=True,
        )
    return ids


async def _newest_message_id(chat_id):
    async for message in userbot.get_chat_history(chat_id, limit=1):
        return message.id
    return 0


async def _backfill_pass(chat_id, progress):
    """Oldest → newest. last_message_id se aage badhta hai. Nayi files live catch karega."""
    scanned = progress.get("scanned", 0)
    forwarded_count = progress.get("forwarded", 0)
    skipped_count = progress.get("skipped", 0)
    dup_count = progress.get("duplicates", 0)
    last_seen_id = int(progress.get("last_message_id", 0) or 0)
    skip_left = int(progress.get("skip_left", 0) or 0)
    cursor = last_seen_id + 1
    if cursor < 1:
        cursor = 1
    newest = await _newest_message_id(chat_id)
    batch = 80

    while cursor <= newest:
        if BACKFILL_CONTROL.get(chat_id) == "stop":
            await _save_progress(
                chat_id, last_message_id=last_seen_id, scanned=scanned,
                forwarded=forwarded_count, skipped=skipped_count,
                duplicates=dup_count, status="stopped",
            )
            BACKFILL_CONTROL.pop(chat_id, None)
            return scanned, forwarded_count, skipped_count, True

        ids = list(range(cursor, min(cursor + batch, newest + 1)))
        try:
            messages = await userbot.get_messages(chat_id, ids)
        except FloodWait as e:
            await asyncio.sleep(int(getattr(e, "value", 1)) + 1)
            continue
        if not isinstance(messages, list):
            messages = [messages]
        batch_end = ids[-1]
        for message in messages:
            mid = getattr(message, "id", None) if message else None
            if mid:
                last_seen_id = max(last_seen_id, mid)
            if not message or getattr(message, "empty", False):
                continue
            scanned += 1
            media = message.video or message.document
            if not media:
                continue
            if _is_short_video(media):
                skipped_count += 1
                continue
            if skip_left > 0:
                skip_left -= 1
                skipped_count += 1
                continue
            if await _already_have_exact_copy(media.file_name, media.file_size):
                dup_count += 1
                continue
            try:
                await _safe_copy(message, caption=_clean_caption(message.caption), label="BACKFILL")
                forwarded_count += 1
            except Exception:
                skipped_count += 1
                logger.exception(f"[USERBOT-BACKFILL] Failed to forward message {message.id}")
        cursor = batch_end + 1
        last_seen_id = max(last_seen_id, batch_end)
        await _save_progress(
            chat_id, last_message_id=last_seen_id, scanned=scanned,
            forwarded=forwarded_count, skipped=skipped_count,
            duplicates=dup_count, status="running", direction="oldest_first",
            skip_left=skip_left,
        )
        if scanned % 200 == 0:
            logger.info(
                f"[USERBOT-BACKFILL] oldest→new id={last_seen_id}/{newest} scanned={scanned} "
                f"forwarded={forwarded_count} dups={dup_count}"
            )
        if cursor > newest:
            newest = await _newest_message_id(chat_id)

    await _save_progress(
        chat_id, last_message_id=last_seen_id, scanned=scanned,
        forwarded=forwarded_count, skipped=skipped_count,
        duplicates=dup_count, status="done",
    )
    return scanned, forwarded_count, skipped_count, False


async def backfill_channel(chat_id, resume=True, start_from=None, skip_files=0):
    """
    Runs continuously until the channel is fully scanned OR the user sends /userbot_stop.
    If an unexpected error happens mid-way, it auto-retries on its own (after a short
    pause) instead of dying and waiting for a manual /userbot_resume.
    Progress is saved after every processed message, so even a crash loses at most
    one message of work — no more duplicate re-forwarding on resume.

    start_from: if given, jumps straight to this message_id (skipping everything
    newer than it) instead of starting from the top or resuming saved progress.
    Useful to skip a range you already know is fully covered.
    """
    if not USERBOT_BACKUP_CHANNEL:
        raise RuntimeError("USERBOT_BACKUP_CHANNEL is not set on Render.")

    if not await _is_live_disabled(chat_id):
        INDEXED_CHAT_IDS.add(chat_id)
        try:
            await db.misc.update_one(
                {"_id": "userbot_live_chats"},
                {"$addToSet": {"chat_ids": chat_id}},
                upsert=True,
            )
        except Exception:
            pass
    BACKFILL_CONTROL[chat_id] = "running"
    retry_delay = 5
    first_pass = True

    while True:
        if first_pass and (start_from is not None or skip_files):
            progress = {
                "last_message_id": max(0, int(start_from or 1) - 1) if start_from else 0,
                "scanned": 0, "forwarded": 0, "skipped": 0, "duplicates": 0,
                "direction": "oldest_first",
                "skip_left": int(skip_files or 0),
            }
        elif first_pass:
            saved = await _get_progress(chat_id) if resume else {}
            if saved.get("direction") == "oldest_first" and not skip_files:
                progress = saved
            else:
                progress = {
                    "last_message_id": 0, "scanned": 0, "forwarded": 0,
                    "skipped": 0, "duplicates": 0, "direction": "oldest_first",
                    "skip_left": int(skip_files or 0),
                }
        else:
            progress = await _get_progress(chat_id)
        first_pass = False
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

    try:
        await Media.collection.create_index([("file_name", 1), ("file_size", 1)], background=True)
        if MULTIPLE_DB:
            await Media2.collection.create_index([("file_name", 1), ("file_size", 1)], background=True)
    except Exception as e:
        logger.warning(f"[USERBOT] Could not ensure duplicate-check index: {e}")

    for target in USERBOT_CHANNELS:
        chat = await _join_target(target)
        # env channel join = access only. Live tabhi ON jab disabled na ho.
        if chat and await _is_live_disabled(chat.id):
            INDEXED_CHAT_IDS.discard(chat.id)
            logger.info(f"[USERBOT] Live kept OFF for {chat.id} (user disabled)")

    try:
        saved = await db.misc.find_one({"_id": "userbot_live_chats"})
        for cid in (saved or {}).get("chat_ids", []):
            try:
                cid_i = int(cid)
            except (TypeError, ValueError):
                continue
            if await _is_live_disabled(cid_i):
                INDEXED_CHAT_IDS.discard(cid_i)
                continue
            if cid_i not in INDEXED_CHAT_IDS:
                chat = await _join_target(str(cid_i))
                if chat:
                    logger.info(f"[USERBOT] Restored live forward for {cid_i}")
    except Exception as e:
        logger.error(f"[USERBOT] Failed to restore live-forward chats: {e}")

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
            logger.info(f"[USERBOT-LIVE] Ignoring message from chat {message.chat.id} ('{message.chat.title}') — not in INDEXED_CHAT_IDS={INDEXED_CHAT_IDS}")
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


async def recover_names_from_channels(limit_per_chat=8000):
    """Read real Telegram file names from backup/source channels and fix DB rows."""
    if not userbot or not userbot.is_connected:
        return {"scanned": 0, "fixed": 0, "skipped": 0, "error": "userbot off"}

    chats = set(INDEXED_CHAT_IDS)
    if USERBOT_BACKUP_CHANNEL:
        chats.add(USERBOT_BACKUP_CHANNEL)
    if not chats:
        return {"scanned": 0, "fixed": 0, "skipped": 0, "error": "no channels"}

    scanned = 0
    fixed = 0
    skipped = 0
    samples = []
    models = [Media]
    if MULTIPLE_DB:
        models.append(Media2)
    JOBS["repairnames"] = {"state": "running", "step": "channel scan", "scanned": 0, "fixed": 0}

    for chat_id in list(chats):
        count = 0
        try:
            async for message in userbot.get_chat_history(chat_id):
                count += 1
                if count > limit_per_chat:
                    break
                media = message.video or message.document
                if not media or not getattr(media, "file_name", None):
                    continue
                scanned += 1
                cleaned = clean_media_filename(media.file_name)
                if not cleaned:
                    skipped += 1
                    continue
                try:
                    packed, _ = unpack_new_file_id(media.file_id)
                except Exception:
                    skipped += 1
                    continue
                updated = False
                for model in models:
                    doc = await model.collection.find_one({"_id": packed}, {"file_name": 1})
                    if not doc:
                        continue
                    current = doc.get("file_name") or ""
                    if current != cleaned and (is_useless_filename(current) or current.lower() in {"bbot", "seeai", "seeai bbot"}):
                        await model.collection.update_one(
                            {"_id": packed},
                            {"$set": {"file_name": cleaned}},
                        )
                        updated = True
                if updated:
                    fixed += 1
                    if len(samples) < 8:
                        samples.append(cleaned[:80])
                if scanned % 50 == 0:
                    JOBS["repairnames"] = {
                        "state": "running",
                        "step": f"channel {chat_id}",
                        "scanned": scanned,
                        "fixed": fixed,
                    }
        except Exception as e:
            logger.error(f"[REPAIR-CHANNEL] chat {chat_id} failed: {e}")

    JOBS["repairnames"] = {"state": "done", "step": "finished", "scanned": scanned, "fixed": fixed}
    return {"scanned": scanned, "fixed": fixed, "skipped": skipped, "samples": samples, "error": None}


STRIP_CONTROL = {}  # chat_id -> "running" | "stop"


async def strip_channel_captions(chat_id, limit=None, wipe=False, bot_client=None, progress_cb=None, resume=True):
    """Edit captions already posted in ONE channel.

    wipe=False → links/@username hatao, title rakho
    wipe=True  → caption khali karo
    Tries userbot first, then the bot account.
    """
    if (not userbot or not userbot.is_connected) and not bot_client:
        return {"scanned": 0, "edited": 0, "skipped": 0, "failed": 0, "error": "userbot off — USER_SESSION check karo"}

    reader = userbot if (userbot and userbot.is_connected) else bot_client

    scanned = 0
    edited = 0
    skipped = 0
    failed = 0
    seen_media = 0
    last_seen_id = 0
    offset_id = 0
    STRIP_CONTROL[chat_id] = "running"
    if resume:
        try:
            saved = await db.misc.find_one({"_id": f"strip_{chat_id}"})
            if saved:
                offset_id = int(saved.get("last_message_id") or 0)
                scanned = int(saved.get("scanned") or 0)
                edited = int(saved.get("edited") or 0)
                skipped = int(saved.get("skipped") or 0)
                failed = int(saved.get("failed") or 0)
        except Exception:
            pass
    JOBS["strip_captions"] = {
        "state": "running",
        "chat_id": chat_id,
        "scanned": 0,
        "edited": 0,
        "failed": 0,
    }
    last_err = None

    async def _edit(msg, new_cap):
        nonlocal last_err
        errors = []
        for client in (userbot if userbot and userbot.is_connected else None, bot_client):
            if not client:
                continue
            try:
                if client is userbot:
                    await msg.edit_caption(new_cap)
                else:
                    await client.edit_message_caption(chat_id, msg.id, new_cap)
                return True
            except FloodWait as e:
                await asyncio.sleep(int(getattr(e, "value", 1)) + 1)
                try:
                    if client is userbot:
                        await msg.edit_caption(new_cap)
                    else:
                        await client.edit_message_caption(chat_id, msg.id, new_cap)
                    return True
                except Exception as e2:
                    errors.append(str(e2))
            except Exception as e:
                errors.append(str(e))
        last_err = errors[-1] if errors else "edit forbidden"
        return False

    try:
        history_kw = {"offset_id": offset_id} if offset_id else {}
        async for message in reader.get_chat_history(chat_id, **history_kw):
            if STRIP_CONTROL.get(chat_id) == "stop":
                break
            scanned += 1
            last_seen_id = message.id
            if limit and seen_media >= limit:
                break
            media = message.video or message.document
            if not media:
                continue
            seen_media += 1
            raw = message.caption or ""
            if wipe:
                new_cap = ""
                if not raw:
                    skipped += 1
                    continue
            else:
                if not raw:
                    skipped += 1
                    continue
                new_cap = _clean_caption(raw) or ""
                if new_cap.strip() == raw.strip():
                    skipped += 1
                    continue
            ok = await _edit(message, new_cap if new_cap else None)
            if ok:
                edited += 1
                await asyncio.sleep(0.35)
            else:
                failed += 1
            if seen_media % 15 == 0 or scanned % 100 == 0:
                JOBS["strip_captions"] = {
                    "state": "running",
                    "chat_id": chat_id,
                    "scanned": scanned,
                    "edited": edited,
                    "skipped": skipped,
                    "failed": failed,
                    "last_message_id": last_seen_id,
                    "last_error": last_err,
                }
                try:
                    await db.misc.update_one(
                        {"_id": f"strip_{chat_id}"},
                        {"$set": {
                            "last_message_id": last_seen_id,
                            "scanned": scanned,
                            "edited": edited,
                            "skipped": skipped,
                            "failed": failed,
                            "status": "running",
                        }},
                        upsert=True,
                    )
                except Exception:
                    pass
                if progress_cb:
                    try:
                        await progress_cb(scanned, edited, skipped, failed, last_seen_id)
                    except Exception:
                        pass
    except Exception as e:
        JOBS["strip_captions"] = {
            "state": "error",
            "chat_id": chat_id,
            "scanned": scanned,
            "edited": edited,
            "skipped": skipped,
            "failed": failed,
            "error": str(e),
        }
        return {
            "scanned": scanned,
            "edited": edited,
            "skipped": skipped,
            "failed": failed,
            "error": str(e),
            "last_error": last_err,
        }
    done_state = "stopped" if STRIP_CONTROL.get(chat_id) == "stop" else "done"
    STRIP_CONTROL.pop(chat_id, None)
    try:
        await db.misc.update_one(
            {"_id": f"strip_{chat_id}"},
            {"$set": {
                "last_message_id": last_seen_id,
                "scanned": scanned,
                "edited": edited,
                "skipped": skipped,
                "failed": failed,
                "status": done_state,
            }},
            upsert=True,
        )
    except Exception:
        pass
    JOBS["strip_captions"] = {
        "state": done_state,
        "chat_id": chat_id,
        "scanned": scanned,
        "edited": edited,
        "skipped": skipped,
        "failed": failed,
        "last_message_id": last_seen_id,
        "last_error": last_err,
    }
    return {
        "scanned": scanned,
        "edited": edited,
        "skipped": skipped,
        "failed": failed,
        "error": None,
        "last_error": last_err,
    }
