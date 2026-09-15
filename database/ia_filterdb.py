import logging
from struct import pack
import re
import base64
from pyrogram.file_id import FileId
from typing import Dict, List
from collections import defaultdict
from pymongo.errors import DuplicateKeyError
from umongo import Instance, Document, fields
from motor.motor_asyncio import AsyncIOMotorClient
from marshmallow import ValidationError
from info import *
from utils import get_settings, save_group_settings, strip_channel_tags
from datetime import datetime, timedelta
import logging
import asyncio

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
# ---------------------------------------------------------

_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_SEASON_TOKEN_RE = re.compile(r'^(?:s|se|ssn|season)0*(\d{1,2})$', re.IGNORECASE)
_EPISODE_TOKEN_RE = re.compile(r'^(?:e|ep|eps|episode)0*(\d{1,3})$', re.IGNORECASE)
_SKIP_SEARCH_TOKENS = {
    "mkv", "mp4", "avi", "mov", "webm", "the", "a", "an", "and", "of", "in", "on",
    "dual", "audio", "multi", "bluray", "webrip", "webdl", "hdrip",
}
_LANG_ALIASES = {
    "hinid": "hindi", "hidi": "hindi", "hndi": "hindi", "hind": "hindi",
    "hin": "hindi", "hindhi": "hindi",
    "engilsh": "english", "englsh": "english", "eng": "english",
    "tam": "tamil", "tml": "tamil",
    "tel": "telugu", "telgu": "telugu",
    "mal": "malayalam", "mallu": "malayalam",
    "kan": "kannada",
}
_USELESS_NAME_RE = re.compile(r'^\.?(mkv|mp4|avi|mov|webm|m4v|ts|zip|rar|iso)?$', re.IGNORECASE)
_HTML_TAG_RE = re.compile(r'<[^>]+>')


def is_useless_filename(name: str) -> bool:
    n = re.sub(r"\s+", " ", str(name or "")).strip()
    return (not n) or bool(_USELESS_NAME_RE.fullmatch(n)) or len(n) <= 3


def normalize_search_text(text: str) -> str:
    """Turn any messy title into plain words so dots/symbols don't break search."""
    if not text:
        return ""
    text = str(text).replace("'", "")
    text = _PUNCT_RE.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def collapse_season_episode_tokens(tokens):
    """season 4 / s 04 / s-2 / episode 3 → s4 / ep3 so variants match."""
    out = []
    i = 0
    while i < len(tokens):
        t = tokens[i].lower()
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if t in {"s", "se", "ssn", "season"} and re.fullmatch(r'\d{1,2}', nxt):
            out.append(f"s{int(nxt)}")
            i += 2
            continue
        if t in {"e", "ep", "eps", "episode"} and re.fullmatch(r'\d{1,3}', nxt):
            out.append(f"ep{int(nxt)}")
            i += 2
            continue
        out.append(tokens[i])
        i += 1
    return out


def _one_typo_regex(word: str) -> str:
    """Allow 1 spelling mistake: wrong letter or two letters swapped."""
    word = word.lower()
    if len(word) < 4 or len(word) > 14:
        return re.escape(word)
    alts = [re.escape(word)]
    for i in range(len(word)):
        alts.append(re.escape(word[:i]) + r'.' + re.escape(word[i + 1:]))
        if i + 1 < len(word):
            swapped = word[:i] + word[i + 1] + word[i] + word[i + 2:]
            alts.append(re.escape(swapped))
    # unique, keep regex size sane
    uniq = list(dict.fromkeys(alts))[:40]
    return '(?:' + '|'.join(uniq) + ')'


def _token_to_regex(token: str, fuzzy: bool = False) -> str:
    token = token.strip()
    if not token:
        return ""
    sm = _SEASON_TOKEN_RE.fullmatch(token)
    if sm:
        num = int(sm.group(1))
        return rf"(?:s(?:eason|e|sn)?[\s._-]*0*{num}|season[\s._-]*0*{num})"
    em = _EPISODE_TOKEN_RE.fullmatch(token)
    if em:
        num = int(em.group(1))
        return rf"(?:e(?:p(?:isode)?)?[\s._-]*0*{num}|episode[\s._-]*0*{num})"
    if fuzzy:
        return _one_typo_regex(token)
    return re.escape(token)


def _query_tokens(query: str):
    cleaned = normalize_search_text(query)
    if not cleaned:
        return []
    raw_tokens = cleaned.split()
    tokens = []
    for t in raw_tokens:
        low = t.lower()
        if low in _SKIP_SEARCH_TOKENS:
            continue
        tokens.append(_LANG_ALIASES.get(low, t))
    tokens = collapse_season_episode_tokens(tokens)
    tokens = [t for t in tokens if t.lower() not in _SKIP_SEARCH_TOKENS]
    return tokens or collapse_season_episode_tokens(raw_tokens)


def build_strict_pattern(query: str):
    """All typed words must match. No fuzzy, no extra similar titles."""
    tokens = _query_tokens(query)
    if not tokens:
        return None
    parts = [_token_to_regex(t, fuzzy=False) for t in tokens[:12]]
    parts = [p for p in parts if p]
    if not parts:
        return None
    return r".*?".join(parts)


def build_flexible_pattern(query: str):
    """Fallback only: S02 vs Season 2 and small spelling mistakes."""
    tokens = _query_tokens(query)
    if not tokens:
        return None

    core_tokens = []
    seen_episode = False
    for t in tokens:
        core_tokens.append(t)
        if _EPISODE_TOKEN_RE.fullmatch(t):
            seen_episode = True
            break
    if not seen_episode:
        core_tokens = tokens[:6]

    def join_tokens(toks, fuzzy=False):
        # Fuzzy only the longest title words, never season/episode numbers
        fuzzy_ids = set()
        if fuzzy:
            ranked = [
                i for i, t in enumerate(toks)
                if 4 <= len(t) <= 14
                and not _SEASON_TOKEN_RE.fullmatch(t)
                and not _EPISODE_TOKEN_RE.fullmatch(t)
            ]
            fuzzy_ids = set(ranked[:5])
        parts = []
        for i, t in enumerate(toks):
            part = _token_to_regex(t, fuzzy=(i in fuzzy_ids))
            if part:
                parts.append(part)
        if not parts:
            return None
        return r".*?".join(parts)

    patterns = []
    for toks, use_fuzzy in (
        (tokens[:10], False),
        (core_tokens, False),
        (core_tokens, True),
        (tokens[:10], True),
    ):
        pat = join_tokens(toks, fuzzy=use_fuzzy)
        if pat and pat not in patterns:
            patterns.append(pat)
    if not patterns:
        return None
    return "(?:" + ")|(?:".join(patterns) + ")"


def name_from_caption(caption: str) -> str:
    if not caption:
        return ""
    text = strip_channel_tags(str(caption))
    text = _HTML_TAG_RE.sub(" ", text)
    text = re.sub(r"[_\-\.#+$%^&*()!~`,;:\"'?/<>\[\]{}=|\\@]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return "" if is_useless_filename(text) else text


def clean_media_filename(raw_name: str) -> str:
    if not raw_name:
        return ""
    text = strip_channel_tags(str(raw_name))
    text = re.sub(r"[_\-\.#+$%^&*()!~`,;:\"'?/<>\[\]{}=|\\@]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return "" if is_useless_filename(text) else text

# Global cache for DB size
_db_stats_cache = {"timestamp": None, "primary_size": 0.0}

# Primary DB
client = AsyncIOMotorClient(DATABASE_URI)
db = client[DATABASE_NAME]
instance = Instance.from_db(db)

# secondary db
client2 = AsyncIOMotorClient(DATABASE_URI2)
db2 = client2[DATABASE_NAME]
instance2 = Instance.from_db(db2)


@instance.register
class Media(Document):
    file_id = fields.StrField(attribute="_id")
    file_ref = fields.StrField(allow_none=True)
    file_name = fields.StrField(required=True)
    file_size = fields.IntField(required=True)
    file_type = fields.StrField(allow_none=True)
    mime_type = fields.StrField(allow_none=True)
    caption = fields.StrField(allow_none=True)

    class Meta:
        indexes = ("$file_name",)
        collection_name = COLLECTION_NAME


@instance2.register
class Media2(Document):
    file_id = fields.StrField(attribute="_id")
    file_ref = fields.StrField(allow_none=True)
    file_name = fields.StrField(required=True)
    file_size = fields.IntField(required=True)
    file_type = fields.StrField(allow_none=True)
    mime_type = fields.StrField(allow_none=True)
    caption = fields.StrField(allow_none=True)

    class Meta:
        indexes = ("$file_name",)
        collection_name = COLLECTION_NAME


async def check_db_size(db):
    try:
        now = datetime.utcnow()
        cache_stale_by_time = _db_stats_cache["timestamp"] is None or (
            now - _db_stats_cache["timestamp"] > timedelta(minutes=10)
        )
        refresh_if_size_threshold = _db_stats_cache["primary_size"] >= 10.0
        if not cache_stale_by_time and not refresh_if_size_threshold:
            return _db_stats_cache["primary_size"]
        stats = await db.command("dbstats")
        db_logical_size = stats["dataSize"]
        db_index_size = stats["indexSize"]
        db_logical_size_mb = db_logical_size / (1024 * 1024)
        db_index_size_mb = db_index_size / (1024 * 1024)
        db_size_mb = db_logical_size_mb + db_index_size_mb
        _db_stats_cache["primary_size"] = db_size_mb
        _db_stats_cache["timestamp"] = now
        return db_size_mb
    except Exception as e:
        print(f"Error Checking Database Size: {e}")
        return 0


async def save_file(media):
    """Save file in database, with detailed logging."""
    file_id, file_ref = unpack_new_file_id(media.file_id)
    raw_name = str(media.file_name or "")
    raw_name = re.sub(r'^@([A-Za-z][A-Za-z0-9]{2,31})_', '', raw_name)
    file_name = re.sub(
        r"[_\-\.#+$%^&*()!~`,;:\"'?/<>\[\]{}=|\\@]", " ", raw_name
    )
    file_name = re.sub(r"\s+", " ", file_name).strip()
    if is_useless_filename(file_name):
        cap_src = None
        try:
            cap_src = media.caption.html if getattr(media, "caption", None) else None
        except Exception:
            cap_src = str(getattr(media, "caption", "") or "")
        recovered = name_from_caption(cap_src or "")
        if recovered:
            file_name = recovered
    saveMedia = Media
    target_db = "Primary"
    if MULTIPLE_DB:
        try:
            exists = await Media.count_documents({"file_id": file_id}, limit=1)
            if exists:
                logger.info(f"[SKIP] '{file_name}' already in Primary DB.")
                return False, 0
            primary_db_size = await check_db_size(db)
            if primary_db_size >= 407:
                saveMedia = Media2
                target_db = "Secondary"
                logger.warning("Switching to Secondary DB due to size threshold.")
        except Exception as e:
            logger.error(
                "Error during MULTIPLE_DB check; defaulting to primary DB.", exc_info=e
            )
    try:
        record = saveMedia(
            file_id=file_id,
            file_ref=file_ref,
            file_name=file_name,
            file_size=media.file_size,
            file_type=media.file_type,
            mime_type=media.mime_type,
            caption=(media.caption.html if media.caption and INDEX_CAPTION else None),
        )
    except ValidationError as e:
        logger.exception(f"[VALIDATION ERROR] '{file_name}' → {e}")
        return False, 2
    try:
        await record.commit()
    except DuplicateKeyError:
        logger.info(
            f"[SKIP] DuplicateKey: '{file_name}' already exists in {target_db} DB."
        )
        return False, 0
    except Exception as e:
        logger.exception(
            f"[ERROR] Failed commit of '{file_name}' to {target_db} DB.", exc_info=e
        )
        return False, 3
    logger.info(f"[SUCCESS] '{file_name}' saved to {target_db} DB.")
    return True, 1

async def get_search_results(chat_id, query, file_type=None, max_results=None, offset=0, filter=False):
    if chat_id is not None:
        settings = await get_settings(int(chat_id))
        if max_results is None:
            try:
                max_results = 10 if settings.get("max_btn") else int(MAX_B_TN)
            except KeyError:
                await save_group_settings(int(chat_id), "max_btn", True)
                settings = await get_settings(int(chat_id))
                max_results = 10 if settings.get("max_btn") else int(MAX_B_TN)

    _fallback_fuzzy = False
    if isinstance(query, list):
        compiled = []
        for q in query:
            pat = build_flexible_pattern(q) or re.escape(normalize_search_text(q) or q)
            try:
                compiled.append(re.compile(pat, re.IGNORECASE))
            except re.error:
                continue
        if not compiled:
            return [], None, 0
        name_filters = [{"file_name": r} for r in compiled]
        if USE_CAPTION_FILTER:
            name_filters += [{"caption": r} for r in compiled]
        filter_mongo = {"$or": name_filters}
    else:
        query = (query or "").strip()
        if not query:
            return [], None, 0

        strict_pat = build_strict_pattern(query)
        fuzzy_pat = build_flexible_pattern(query)
        raw_pattern = strict_pat or fuzzy_pat
        if not raw_pattern:
            cleaned = normalize_search_text(query)
            raw_pattern = re.escape(cleaned or query)

        try:
            regex = re.compile(raw_pattern, flags=re.IGNORECASE)
        except re.error:
            return [], None, 0

        if USE_CAPTION_FILTER:
            filter_mongo = {"$or": [{"file_name": regex}, {"caption": regex}]}
        else:
            filter_mongo = {"file_name": regex}
        _fallback_fuzzy = bool(strict_pat and fuzzy_pat and fuzzy_pat != strict_pat)

    if file_type:
        filter_mongo["file_type"] = file_type
    
    # The rest of the function remains the same, using parallel queries.
    if ULTRA_FAST_MODE:
        limit = max_results + 1
        find_tasks = [Media.find(filter_mongo).sort("$natural", -1).skip(offset).limit(limit).to_list(length=limit)]
        if MULTIPLE_DB:
            find_tasks.append(Media2.find(filter_mongo).sort("$natural", -1).skip(offset).limit(limit).to_list(length=limit))
        
        results = await asyncio.gather(*find_tasks)
        files = results[0]
        if MULTIPLE_DB and len(results) > 1:
            files.extend(results[1])
        
        files = files[:limit]

        has_next_page = len(files) > max_results
        if has_next_page:
            files = files[:-1]

        next_offset = offset + len(files) if has_next_page else ""
        total_results = offset + len(files) + (1 if has_next_page else 0)
    else:
        count_tasks = [Media.count_documents(filter_mongo)]
        find_tasks = [Media.find(filter_mongo).sort("$natural", -1).skip(offset).limit(max_results).to_list(length=max_results)]

        if MULTIPLE_DB:
            count_tasks.append(Media2.count_documents(filter_mongo))
            find_tasks.append(Media2.find(filter_mongo).sort("$natural", -1).skip(offset).limit(max_results).to_list(length=max_results))
        
        count_results, find_results = await asyncio.gather(
            asyncio.gather(*count_tasks),
            asyncio.gather(*find_tasks)
        )
        
        total_results = sum(count_results)
        files = find_results[0]
        if MULTIPLE_DB and len(find_results) > 1:
            files.extend(find_results[1])
        
        files = files[:max_results]
        
        next_offset = offset + len(files)
        if next_offset >= total_results:
            next_offset = ""

    if (not files) and _fallback_fuzzy and offset == 0:
        try:
            regex = re.compile(fuzzy_pat, flags=re.IGNORECASE)
        except re.error:
            return files, next_offset, total_results
        if USE_CAPTION_FILTER:
            filter_mongo = {"$or": [{"file_name": regex}, {"caption": regex}]}
        else:
            filter_mongo = {"file_name": regex}
        if file_type:
            filter_mongo["file_type"] = file_type
        if ULTRA_FAST_MODE:
            limit = max_results + 1
            find_tasks = [Media.find(filter_mongo).sort("$natural", -1).skip(0).limit(limit).to_list(length=limit)]
            if MULTIPLE_DB:
                find_tasks.append(Media2.find(filter_mongo).sort("$natural", -1).skip(0).limit(limit).to_list(length=limit))
            results = await asyncio.gather(*find_tasks)
            files = results[0]
            if MULTIPLE_DB and len(results) > 1:
                files.extend(results[1])
            files = files[:limit]
            has_next_page = len(files) > max_results
            if has_next_page:
                files = files[:-1]
            next_offset = len(files) if has_next_page else ""
            total_results = len(files) + (1 if has_next_page else 0)
        else:
            count_tasks = [Media.count_documents(filter_mongo)]
            find_tasks = [Media.find(filter_mongo).sort("$natural", -1).limit(max_results).to_list(length=max_results)]
            if MULTIPLE_DB:
                count_tasks.append(Media2.count_documents(filter_mongo))
                find_tasks.append(Media2.find(filter_mongo).sort("$natural", -1).limit(max_results).to_list(length=max_results))
            count_results, find_results = await asyncio.gather(
                asyncio.gather(*count_tasks),
                asyncio.gather(*find_tasks),
            )
            total_results = sum(count_results)
            files = find_results[0]
            if MULTIPLE_DB and len(find_results) > 1:
                files.extend(find_results[1])
            files = files[:max_results]
            next_offset = len(files)
            if next_offset >= total_results:
                next_offset = ""

    return files, next_offset, total_results

async def get_bad_files(query, file_type=None):
    query = query.strip()
    if not query:
        raw_pattern = '.'
    elif ' ' not in query:
        raw_pattern = r"(\b|[\.\+\-_])" + query + r"(\b|[\.\+\-_])"
    else:
        raw_pattern = query.replace(" ", r".*[\s\.\+\-_()]")
    try:
        regex = re.compile(raw_pattern, flags=re.IGNORECASE)
    except:
        return []
    if USE_CAPTION_FILTER:
        filter = {'$or': [{'file_name': regex}, {'caption': regex}]}
    else:
        filter = {'file_name': regex}
    if file_type:
        filter['file_type'] = file_type
    cursor1 = Media.find(filter).sort('$natural', -1)
    files1 = await cursor1.to_list(length=(await Media.count_documents(filter)))
    if MULTIPLE_DB:
        cursor2 = Media2.find(filter).sort('$natural', -1)
        files2 = await cursor2.to_list(length=(await Media2.count_documents(filter)))
        files = files1 + files2
    else:
        files = files1
    total_results = len(files)
    return files, total_results


async def get_file_details(query):
    filter = {"file_id": query}
    
    tasks = [Media.find(filter).to_list(length=1)]
    if MULTIPLE_DB:
        tasks.append(Media2.find(filter).to_list(length=1))
        
    results = await asyncio.gather(*tasks)
    
    for filedetails in results:
        if filedetails:
            return filedetails
            
    return []


def encode_file_id(s: bytes) -> str:
    r = b""
    n = 0
    for i in s + bytes([22]) + bytes([4]):
        if i == 0:
            n += 1
        else:
            if n:
                r += b"\x00" + bytes([n])
                n = 0

            r += bytes([i])
    return base64.urlsafe_b64encode(r).decode().rstrip("=")


def encode_file_ref(file_ref: bytes) -> str:
    return base64.urlsafe_b64encode(file_ref).decode().rstrip("=")


def unpack_new_file_id(new_file_id):
    """Return file_id, file_ref"""
    decoded = FileId.decode(new_file_id)
    file_id = encode_file_id(
        pack(
            "<iiqq",
            int(decoded.file_type),
            decoded.dc_id,
            decoded.media_id,
            decoded.access_hash,
        )
    )
    file_ref = encode_file_ref(decoded.file_reference)
    return file_id, file_ref


async def dreamxbotz_fetch_media(limit: int) -> List[dict]:
    try:
        if MULTIPLE_DB:
            db_size = await check_db_size(Media)
            if db_size > 407:
                cursor = Media2.find().sort("$natural", -1).limit(limit)
                files = await cursor.to_list(length=limit)
                return files
        cursor = Media.find().sort("$natural", -1).limit(limit)
        files = await cursor.to_list(length=limit)
        return files
    except Exception as e:
        logger.error(f"Error in dreamxbotz_fetch_media: {e}")
        return []


async def dreamxbotz_clean_title(filename: str, is_series: bool = False) -> str:
    try:
        year_match = re.search(r"^(.*?(\d{4}|\(\d{4}\)))", filename, re.IGNORECASE)
        if year_match:
            title = year_match.group(1).replace("(", "").replace(")", "")
            return (
                re.sub(
                    r"(?:@[^ \n\r\t.,:;!?()\[\]{}<>\\\/\"'=_%]+|[._\-\[\]@()]+)",
                    " ",
                    title,
                )
                .strip()
                .title()
            )
        if is_series:
            season_match = re.search(
                r"(.*?)(?:S(\d{1,2})|Season\s*(\d+)|Season(\d+))(?:\s*Combined)?",
                filename,
                re.IGNORECASE,
            )
            if season_match:
                title = season_match.group(1).strip()
                season = (
                    season_match.group(2)
                    or season_match.group(3)
                    or season_match.group(4)
                )
                title = (
                    re.sub(
                        r"(?:@[^ \n\r\t.,:;!?()\[\]{}<>\\\/\"'=_%]+|[._\-\[\]@()]+)",
                        " ",
                        title,
                    )
                    .strip()
                    .title()
                )
                return f"{title} S{int(season):02}"
        title = filename
        return (
            re.sub(
                r"(?:@[^ \n\r\t.,:;!?()\[\]{}<>\\\/\"'=_%]+|[._\-\[\]@()]+)", " ", title
            )
            .strip()
            .title()
        )
    except Exception as e:
        logger.error(f"Error in truncate_title: {e}")
        return filename


async def dreamxbotz_get_movies(limit: int = 20) -> List[str]:
    try:
        cursor = await dreamxbotz_fetch_media(limit * 2)
        results = set()
        pattern = r"(?:s\d{1,2}|season\s*\d+|season\d+)(?:\s*combined)?(?:e\d{1,2}|episode\s*\d+)?\b"
        for file in cursor:
            file_name = getattr(file, "file_name", "")
            if not re.search(pattern, file_name, re.IGNORECASE):
                title = await dreamxbotz_clean_title(file_name)
                results.add(title)
            if len(results) >= limit:
                break
        return sorted(list(results))[:limit]
    except Exception as e:
        logger.error(f"Error in dreamxbotz_get_movies: {e}")
        return []


async def dreamxbotz_get_series(limit: int = 30) -> Dict[str, List[int]]:
    try:
        cursor = await dreamxbotz_fetch_media(limit * 5)
        grouped = defaultdict(list)
        pattern = r"(.*?)(?:S(\d{1,2})|Season\s*(\d+)|Season(\d+))(?:\s*Combined)?(?:E(\d{1,2})|Episode\s*(\d+))?\b"
        for file in cursor:
            file_name = getattr(file, "file_name", "")
            match = re.search(pattern, file_name, re.IGNORECASE)
            if match:
                title = await dreamxbotz_clean_title(match.group(1), is_series=True)
                season = int(match.group(2) or match.group(3) or match.group(4))
                grouped[title].append(season)
        return {
            title: sorted(set(seasons))[:10]
            for title, seasons in grouped.items()
            if seasons
        }
    except Exception as e:
        logger.error(f"Error in dreamxbotz_get_series: {e}")
        return []


_BAD_NAME_FILTER = {
    "file_name": {
        "$regex": (
            r"(^\s*\.?((mkv|mp4|avi|mov|webm|m4v|ts|zip|rar))?\s*$)"
            r"|(^(bbot|seeai)(\s+bbot)?(\s+\.?mkv)?$)"
        ),
        "$options": "i",
    }
}


async def repair_broken_filenames():
    """Fix rows saved as `.mkv` / empty / leftover bot-name using caption."""
    models = [Media]
    if MULTIPLE_DB:
        models.append(Media2)
    scanned = 0
    fixed = 0
    unrecoverable = 0
    samples_fixed = []
    samples_bad = []
    for model in models:
        cursor = model.find(_BAD_NAME_FILTER)
        docs = await cursor.to_list(length=50000)
        for doc in docs:
            scanned += 1
            recovered = name_from_caption(getattr(doc, "caption", None) or "")
            if not recovered:
                recovered = clean_media_filename(getattr(doc, "file_name", "") or "")
            if not recovered:
                unrecoverable += 1
                if len(samples_bad) < 8:
                    samples_bad.append(str(getattr(doc, "file_name", ""))[:80])
                continue
            try:
                await model.collection.update_one(
                    {"_id": doc.file_id},
                    {"$set": {"file_name": recovered}},
                )
                fixed += 1
                if len(samples_fixed) < 8:
                    samples_fixed.append(recovered[:80])
            except Exception as e:
                logger.error(f"[REPAIR] Failed to update {doc.file_id}: {e}")
                unrecoverable += 1
    return {
        "scanned": scanned,
        "fixed": fixed,
        "unrecoverable": unrecoverable,
        "samples_fixed": samples_fixed,
        "samples_bad": samples_bad,
    }
