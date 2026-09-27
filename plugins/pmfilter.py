from utils import get_size, is_subscribed, is_req_subscribed, group_setting_buttons, get_poster, get_posterx, temp, get_settings, save_group_settings, get_cap, imdb, is_check_admin, extract_request_content, log_error, clean_filename, generate_season_variations, clean_search_text, format_file_caption, extract_language
import tracemalloc
from fuzzywuzzy import process
from dreamxbotz.util.file_properties import get_name, get_hash
from urllib.parse import quote_plus
import logging
from html import escape as _html_escape
from database.ia_filterdb import Media, Media2, get_file_details, get_search_results, get_bad_files
from database.config_db import mdb
from pyrogram.errors import FloodWait, UserIsBlocked, MessageNotModified, PeerIdInvalid, ChatAdminRequired, UserNotParticipant
from pyrogram import Client, filters, enums
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery, InputMediaPhoto, WebAppInfo
from info import *
from Script import script
from pyrogram.errors.exceptions.bad_request_400 import MediaEmpty, PhotoInvalidDimensions, WebpageMediaEmpty
from database.refer import referdb
from database.users_chats_db import db
import asyncio
import re
import aiohttp
import math
import random
import pytz
from datetime import datetime, timedelta
lock = asyncio.Lock()

logger = logging.getLogger(__name__)


def _pro_font(text):
    """MY MOVIES premium typography: readable Unicode bold for key UI labels."""
    table = str.maketrans(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz",
        "𝗔𝗕𝗖𝗗𝗘𝗙𝗚𝗛𝗜𝗝𝗞𝗟𝗠𝗡𝗢𝗣𝗤𝗥𝗦𝗧𝗨𝗩𝗪𝗫𝗬𝗭𝗮𝗯𝗰𝗱𝗲𝗳𝗴𝗵𝗶𝗷𝗸𝗹𝗺𝗻𝗼𝗽𝗾𝗿𝘀𝘁𝘂𝘃𝘄𝘅𝘆𝘇"
    )
    return str(text).translate(table)

def _pro_label(icon, text):
    return f"{icon} {_pro_font(text)}"

def _pro_file_btn(file):
    """Compact, useful file label: quality, language, episode/season, size."""
    name = clean_filename(getattr(file, "file_name", None) or "File")
    raw = name.lower().replace("_", " ").replace(".", " ")
    size = get_size(getattr(file, "file_size", 0) or 0)

    quality = ""
    for tag, show in ((
        ("2160p", "4K"), ("4k", "4K"), ("1440p", "1440P"),
        ("1080p", "1080P"), ("720p", "720P"), ("480p", "480P"), ("360p", "360P"),
    )):
        if tag in raw:
            quality = show
            break

    # Prefer an exact episode token; otherwise show season when present.
    episode = ""
    m = re.search(r"\bS(?:0?\d{1,2})\s*E(?:0?\d{1,3})\b", raw, re.I)
    if m:
        token = re.sub(r"\s+", "", m.group(0)).upper()
        sm = re.search(r"S(\d+)", token)
        em = re.search(r"E(\d+)", token)
        episode = f"S{int(sm.group(1)):02d}E{int(em.group(1)):02d}" if sm and em else token
    else:
        m = re.search(r"\bS(?:eason\s*)?(0?\d{1,2})\b", raw, re.I)
        if m:
            episode = f"S{int(m.group(1)):02d}"

    # Detect common language tags without exposing the long filename.
    langs = []
    language_map = (
        ("hindi", "Hindi"), ("english", "English"), ("tamil", "Tamil"),
        ("telugu", "Telugu"), ("malayalam", "Malayalam"), ("kannada", "Kannada"),
        ("punjabi", "Punjabi"), ("gujarati", "Gujarati"), ("marathi", "Marathi"),
        ("bengali", "Bengali"), ("multi audio", "Multi Audio"), ("dual audio", "Dual Audio"),
    )
    for token, label in language_map:
        if token in raw and label not in langs:
            langs.append(label)
    language = "/".join(langs[:2])

    parts = [x for x in (quality, language, episode, size) if x]
    return "•".join(parts) if parts else size

logger.setLevel(logging.ERROR)

tracemalloc.start()


TIMEZONE = "Asia/Kolkata"
BUTTON = {}
BUTTONS = {}
FRESH = {}
BUTTONS0 = {}
BUTTONS1 = {}
BUTTONS2 = {}
SPELL_CHECK = {}
OWNER_REQ_CACHE = {}
ADMIN_AWAITING_REPLY = {}  # admin_user_id -> req_key, while they're typing a custom reply


# ─────────────────────────────────────────────────────────────────────────────
# MY MOVIES PRO V3 — grouped title-first search UI
# First page: movie titles only.
# Second page: files + Quality / Language / Season / Premium / Send All.
# Existing file/filter callbacks are deliberately reused.
# ─────────────────────────────────────────────────────────────────────────────
PRO_SEARCH = {}          # search_key -> lightweight title/search state
PRO_DETAIL = {}          # detail_key -> movie detail state
PRO_SCAN_BATCH = 40      # DB records per discovery batch
PRO_MAX_SCAN = 400       # hard upper bound on discovery work per search
PRO_TITLE_PAGE = 10      # movie titles shown per page

_PRO_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_PRO_NOISE_RE = re.compile(
    r"\b(?:2160p|1440p|1080p|720p|480p|360p|4k|web[- .]?dl|webrip|web|bluray|blu[ ._-]?ray|brrip|hdrip|hdtv|x264|x265|hevc|av1|aac|ddp?|dts|atmos|dual[ ._-]?audio|multi[ ._-]?audio|hindi|english|tamil|telugu|malayalam|kannada|punjabi|gujarati|marathi|mkv|mp4|avi|mov|yts|ssfilms|moviezzclub|toonflex)\b.*$",
    re.I,
)
_PRO_SOURCE_RE = re.compile(r"^(?:toonflex|moviezzclub|ssfilms|yts)[\s._-]+", re.I)


def _pro_group_title(file_name):
    """Create a title-level identity without merging different episodes."""
    name = re.sub(r"\.[A-Za-z0-9]{1,8}$", "", clean_filename(file_name or "File"))
    name = re.sub(r"[._]+", " ", name)
    name = re.sub(r"\[[^\]]*\]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    name = _PRO_SOURCE_RE.sub("", name).strip(" -._")

    # Series identity must win over year detection.  The old code stopped at
    # the year first, turning every S01E01/S01E02 file into one big "2024" group.
    season_ep = re.search(r"\bS\d{1,2}(?:E\d{1,3})?\b", name, re.I)
    episode = re.search(r"\bE\d{1,3}\b", name, re.I) if not season_ep else None
    if season_ep or episode:
        marker = (season_ep or episode).group(0).upper()
        base = name[:(season_ep or episode).start()].strip(" -._")
        # If the marker occurs before a year, keep the year as part of the base.
        if not base:
            base = name
        base = re.sub(r"\b(?:part|cd|disc|disk)\s*\d{1,3}\b", "", base, flags=re.I)
        base = re.sub(r"\s+", " ", base).strip(" -._")
        return f"{base.title()} {marker}".strip()

    year = _PRO_YEAR_RE.search(name)
    if year:
        name = name[:year.end()]
    else:
        name = _PRO_NOISE_RE.sub("", name)

    name = re.sub(r"\s+(?:(?:part|cd|disc|disk)\s*)?\d{1,3}$", "", name, flags=re.I)
    name = re.sub(r"\s+", " ", name).strip(" -._")
    return name.title() or "Untitled"

def _pro_group_key(title):
    return re.sub(r"[^a-z0-9]+", "", (title or "").lower())


async def _pro_discover_groups(chat_id, search, first_files, first_offset):
    """Read small DB batches and keep only tiny title metadata in RAM."""
    groups = {}
    seen_records = 0
    page = first_files or []
    next_offset = first_offset

    while page and seen_records < PRO_MAX_SCAN:
        for file in page:
            seen_records += 1
            title = _pro_group_title(getattr(file, "file_name", "File"))
            key = _pro_group_key(title)
            if key not in groups:
                groups[key] = {"title": title, "count": 0}
            groups[key]["count"] += 1
            if seen_records >= PRO_MAX_SCAN:
                break

        if not next_offset or seen_records >= PRO_MAX_SCAN:
            break

        try:
            offset = int(next_offset)
        except (TypeError, ValueError):
            break

        page, next_offset, _ = await get_search_results(
            chat_id, search, max_results=PRO_SCAN_BATCH, offset=offset, filter=True
        )

    return list(groups.values()), next_offset


def _pro_search_markup(state):
    """First page: title buttons only. No file controls here by design."""
    groups = state.get("groups", [])
    page = int(state.get("title_page", 0))
    start = page * PRO_TITLE_PAGE
    end = start + PRO_TITLE_PAGE
    visible = groups[start:end]

    rows = []
    for idx in range(start, end):
        if idx >= len(groups):
            break
        group = groups[idx]
        rows.append([
            InlineKeyboardButton(
                f"🎬 {_pro_font(group['title'])}  ·  {group['count']} file{'s' if group['count'] != 1 else ''}",
                callback_data=f"mmt#{state['key']}#{idx}",
            )
        ])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(_pro_label("‹", "Previous"), callback_data=f"mmprev#{state['key']}"))
    if end < len(groups):
        nav.append(InlineKeyboardButton(_pro_label("➜", "Next"), callback_data=f"mmnext#{state['key']}"))
    if nav:
        rows.append(nav)

    rows.append([
        InlineKeyboardButton(_pro_label("💎", "Premium"), url=f"https://t.me/{temp.U_NAME}?start=premium"),
        InlineKeyboardButton(_pro_label("🏠", "Home"), callback_data="ui_home"),
    ])
    return InlineKeyboardMarkup(rows)


def _pro_detail_markup(key, files, next_offset, total_results, req):
    rows = [
        [
            InlineKeyboardButton(_pro_label("💎", "Premium"), url=f"https://t.me/{temp.U_NAME}?start=premium"),
            InlineKeyboardButton(_pro_label("📦", "Send All"), callback_data=f"sendfiles#{key}"),
        ],
        [
            InlineKeyboardButton(_pro_label("🎚", "Quality"), callback_data=f"qualities#{key}"),
            InlineKeyboardButton(_pro_label("🌐", "Language"), callback_data=f"languages#{key}"),
            InlineKeyboardButton(_pro_label("📺", "Season"), callback_data=f"seasons#{key}"),
        ],
    ]
    rows.extend([
        [InlineKeyboardButton(_pro_file_btn(file), callback_data=f"file#{file.file_id}")]
        for file in files
    ])

    nav = []
    if next_offset != "":
        nav.append(InlineKeyboardButton(_pro_label("➜", "Next"), callback_data=f"mfilenext#{key}#{next_offset}"))
    if nav:
        rows.append(nav)
    rows.append([
        InlineKeyboardButton(_pro_label("⬅️", "Back to Movies"), callback_data=f"mback#{PRO_DETAIL[key]['search_key']}"),
        InlineKeyboardButton(_pro_label("🏠", "Home"), callback_data="ui_home"),
    ])
    return InlineKeyboardMarkup(rows)


async def _pro_edit_caption_or_text(message, text, markup):
    """Edit a PRO message without changing media type."""
    try:
        if getattr(message, "photo", None) or getattr(message, "video", None) or getattr(message, "animation", None):
            await message.edit_caption(caption=text, reply_markup=markup, parse_mode=enums.ParseMode.HTML)
        else:
            await message.edit_text(text=text, reply_markup=markup, disable_web_page_preview=True, parse_mode=enums.ParseMode.HTML)
    except MessageNotModified:
        pass


def _pro_search_caption(state):
    groups = state.get("groups", [])
    query_text = _html_escape(str(state.get("query") or "Movie"))
    page = int(state.get("title_page", 0)) + 1
    total_pages = max(1, math.ceil(len(groups) / PRO_TITLE_PAGE))
    start = int(state.get("title_page", 0)) * PRO_TITLE_PAGE + 1
    end = min(start + PRO_TITLE_PAGE - 1, len(groups))
    shown = max(0, end - start + 1) if groups else 0
    return (
        "┏━━━━━━━━━━━━━━━━━━━━━━┓\n"
        f"        🎬 <b>{_pro_font('SEARCH RESULTS')}</b>\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━┛\n\n"
        f"🔎 <b>{query_text}</b>\n"
        f"📂 <b>{len(groups)}</b> movie titles found\n"
        f"📄 Page <b>{page}/{total_pages}</b> • Showing <b>{shown}</b>\n\n"
        f"💡 <b>{_pro_font('Quick Tip')}:</b> Movie name par tap karein —\n"
        "   next page par available files milengi."
    )


async def _pro_get_season_meta(base_title, season_number, fallback_meta=None):
    """Fetch season-specific TMDB poster/details, with series metadata fallback.
    Uses one search + one season endpoint call and never downloads the image itself.
    """
    fallback_meta = dict(fallback_meta or {})
    if not TMDB_API_KEY:
        return fallback_meta
    try:
        base = str(base_title or "").strip()
        year_match = re.search(r"\b(?:19|20)\d{2}\b", base)
        year = year_match.group(0) if year_match else None
        clean = re.sub(r"\b(?:19|20)\d{2}\b", "", base)
        clean = re.sub(r"\s+", " ", clean).strip(" -")
        if not clean:
            clean = base
        async with aiohttp.ClientSession() as session:
            params = {"api_key": TMDB_API_KEY, "query": clean, "include_adult": "false"}
            if year:
                params["first_air_date_year"] = year
            async with session.get("https://api.themoviedb.org/3/search/tv", params=params, timeout=8) as resp:
                if resp.status != 200:
                    return fallback_meta
                data = await resp.json()
            results = data.get("results") or []
            if not results:
                return fallback_meta
            tv = results[0]
            tv_id = tv.get("id")
            if not tv_id:
                return fallback_meta
            season_url = f"https://api.themoviedb.org/3/tv/{tv_id}/season/{int(season_number)}"
            async with session.get(season_url, params={"api_key": TMDB_API_KEY}, timeout=8) as resp:
                if resp.status != 200:
                    return fallback_meta
                season = await resp.json()

        poster_path = season.get("poster_path")
        backdrop_path = season.get("backdrop_path")
        meta = dict(fallback_meta)
        meta.update({
            "title": tv.get("name") or meta.get("title") or base,
            "year": (season.get("air_date") or tv.get("first_air_date") or "")[:4] or meta.get("year"),
            "rating": str(season.get("vote_average") or tv.get("vote_average") or meta.get("rating") or "N/A"),
            "runtime": meta.get("runtime") or "N/A",
            "genres": meta.get("genres") or "N/A",
            "poster": f"https://image.tmdb.org/t/p/w780{poster_path}" if poster_path else meta.get("poster"),
            "backdrop": f"https://image.tmdb.org/t/p/w1280{backdrop_path}" if backdrop_path else meta.get("backdrop"),
            "season_number": int(season_number),
            "episode_count": season.get("episode_count") or 0,
            "air_date": season.get("air_date") or "",
        })
        return meta
    except Exception as exc:
        logger.debug("Season metadata lookup failed: %s", exc)
        return fallback_meta


def _pro_detail_display_title(state, meta, fallback_title):
    season_number = state.get("season_number")
    base = str(meta.get("title") or fallback_title or "Movie")
    if season_number:
        return f"{base} — Season {int(season_number)}"
    return base

def _pro_detail_caption(title, total, meta, files, display_title=None):
    safe_title = _html_escape(str(display_title or meta.get("title") or title or "Movie"))
    year = meta.get("year") or "N/A"
    rating = meta.get("rating") or "N/A"
    genres = meta.get("genres") or "N/A"
    runtime = meta.get("runtime") or "N/A"
    combined = " ".join(
        f"{getattr(f, 'file_name', '')} {getattr(f, 'caption', '') or ''}" for f in (files or [])
    )
    try:
        detected = extract_language(combined) if combined else ""
    except Exception:
        detected = ""
    languages = detected if detected and str(detected).upper() not in {"N/A", "Nᴏᴛ Aᴠᴀɪʟᴀʙʟᴇ"} else (meta.get("languages") or "N/A")
    if isinstance(languages, (list, tuple)):
        languages = " • ".join(str(x) for x in languages if x) or "N/A"
    return (
        "┏━━━━━━━━━━━━━━━━━━━━━━┓\n"
        "          🎬 <b>MOVIE</b>\n"
        "┗━━━━━━━━━━━━━━━━━━━━━━┛\n\n"
        f"<b>{safe_title}</b>\n"
        f"{year}\n\n"
        f"⭐ <b>{rating}/10</b>\n"
        f"🎭 {genres}\n"
        f"⏱ {runtime}\n"
        f"🌐 {languages}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📂 <b>{_pro_font('AVAILABLE FILES')}</b>"
    )


async def _pro_show_search(client, query, key):
    state = PRO_SEARCH.get(key)
    if not state:
        return await query.answer("⚠️ Search expired. Please search again.", show_alert=True)
    caption = _pro_search_caption(state)
    state["caption"] = caption
    # Keep the search poster while paging and when returning from a movie.
    if getattr(query.message, "photo", None):
        await query.message.edit_caption(
            caption=caption, reply_markup=_pro_search_markup(state),
            parse_mode=enums.ParseMode.HTML
        )
    else:
        poster = (state.get("meta") or {}).get("poster") or (state.get("meta") or {}).get("backdrop")
        if poster:
            try:
                await query.message.delete()
            except Exception:
                pass
            await client.send_photo(
                chat_id=query.message.chat.id, photo=poster, caption=caption,
                reply_markup=_pro_search_markup(state), parse_mode=enums.ParseMode.HTML
            )
        else:
            await _pro_edit_caption_or_text(query.message, caption, _pro_search_markup(state))
    await query.answer()


async def _pro_render_detail(client, query, key, offset=0, push_history=True):
    state = PRO_DETAIL.get(key) or {}
    title = state.get("title") or FRESH.get(key)
    if not title:
        return await query.answer("⚠️ This movie request has expired. Please search again.", show_alert=True)
    try:
        offset = int(offset)
    except (TypeError, ValueError):
        offset = 0

    active_query = state.get("active_query") or title
    files, next_offset, total = await get_search_results(
        query.message.chat.id, active_query, offset=offset, filter=True
    )
    if not files:
        return await query.answer("🚫 No files found for this page.", show_alert=True)

    FRESH[key] = active_query
    temp.GETALL[key] = files
    temp.SHORT[query.from_user.id] = query.message.chat.id
    if push_history:
        history = state.setdefault("history", [0])
        if not history or history[-1] != offset:
            history.append(offset)

    meta = state.get("meta")
    if meta is None:
        try:
            if TMDB_POSTER:
                meta = await get_posterx(title, file=getattr(files[0], "file_name", None))
            else:
                meta = await get_poster(title, file=getattr(files[0], "file_name", None))
        except Exception:
            meta = None
        state["meta"] = meta or {}
        state.setdefault("base_meta", dict(state["meta"]))

    meta = state.get("meta") or {}
    display_title = _pro_detail_display_title(state, meta, title)
    caption = _pro_detail_caption(title, total, meta, files, display_title=display_title)
    markup = _pro_detail_markup(key, files, next_offset, total, query.from_user.id)
    history = state.get("history", [0])
    if len(history) > 1:
        markup.inline_keyboard.insert(2, [
            InlineKeyboardButton(_pro_label("‹", "Previous Files"), callback_data=f"mfileprev#{key}")
        ])

    # First visit: create a real poster message. Later pages: edit its caption/buttons.
    desired_poster = (state.get("meta") or {}).get("poster") or (state.get("meta") or {}).get("backdrop")
    current_poster = None
    try:
        if getattr(query.message, "photo", None):
            current_poster = query.message.photo.file_id if query.message.photo else None
    except Exception:
        current_poster = None

    # If the detail is already a photo message and the selected season has a
    # different poster URL, replace the media instead of leaving the old poster.
    if getattr(query.message, "photo", None) and desired_poster and state.get("poster_url") != desired_poster:
        try:
            await query.message.edit_media(
                InputMediaPhoto(media=desired_poster, caption=caption, parse_mode=enums.ParseMode.HTML),
                reply_markup=markup
            )
            state["poster_url"] = desired_poster
            state["message_id"] = query.message.id
            await query.answer()
            return
        except Exception:
            pass

    if state.get("message_id") != getattr(query.message, "id", None) or not getattr(query.message, "photo", None):
        # If the current message is the old search text, remove it and replace it with the detail poster.
        if not getattr(query.message, "photo", None):
            try:
                await query.message.delete()
            except Exception:
                pass
            poster = (state.get("meta") or {}).get("poster") or (state.get("meta") or {}).get("backdrop")
            sent = None
            if poster:
                try:
                    sent = await client.send_photo(
                        chat_id=query.message.chat.id,
                        photo=poster,
                        caption=caption,
                        reply_markup=markup,
                        parse_mode=enums.ParseMode.HTML,
                    )
                except Exception:
                    sent = None
            if sent is None:
                sent = await client.send_message(
                    chat_id=query.message.chat.id,
                    text=caption,
                    reply_markup=markup,
                    disable_web_page_preview=True,
                    parse_mode=enums.ParseMode.HTML,
                )
            state["message_id"] = sent.id
            state["poster_url"] = poster
        else:
            state["message_id"] = query.message.id
            state["poster_url"] = desired_poster
    else:
        await _pro_edit_caption_or_text(query.message, caption, markup)
    await query.answer()


async def _pro_show_movie(client, query, key, index):
    state = PRO_SEARCH.get(key)
    if not state:
        return await query.answer("⚠️ Search expired. Please search again.", show_alert=True)
    try:
        index = int(index)
    except (TypeError, ValueError):
        return await query.answer("⚠️ Invalid movie selection.", show_alert=True)
    groups = state.get("groups", [])
    if index < 0 or index >= len(groups):
        return await query.answer("⚠️ Movie is no longer available.", show_alert=True)

    title = groups[index]["title"]
    detail_key = f"{key}:m{index}"
    FRESH[detail_key] = title
    BUTTONS.pop(detail_key, None)
    PRO_DETAIL[detail_key] = {
        "search_key": key,
        "title": title,
        "active_query": title,
        "history": [0],
        "meta": None,
        "season_number": None,
        "user_id": query.from_user.id if query.from_user else 0,
    }
    await _pro_render_detail(client, query, detail_key, 0, push_history=False)


@Client.on_callback_query(filters.regex(r"^mmt#"))
async def mymovies_title_cb(client, query):
    _, key, index = query.data.split("#", 2)
    await _pro_show_movie(client, query, key, index)


@Client.on_callback_query(filters.regex(r"^mmnext#"))
async def mymovies_next_titles_cb(client, query):
    key = query.data.split("#", 1)[1]
    state = PRO_SEARCH.get(key)
    if not state:
        return await query.answer("⚠️ Search expired. Please search again.", show_alert=True)
    if (state.get("title_page", 0) + 1) * PRO_TITLE_PAGE >= len(state.get("groups", [])):
        return await query.answer("No more titles found.")
    state["title_page"] += 1
    await _pro_show_search(client, query, key)


@Client.on_callback_query(filters.regex(r"^mmprev#"))
async def mymovies_prev_titles_cb(client, query):
    key = query.data.split("#", 1)[1]
    state = PRO_SEARCH.get(key)
    if not state:
        return await query.answer("⚠️ Search expired. Please search again.", show_alert=True)
    state["title_page"] = max(0, state.get("title_page", 0) - 1)
    await _pro_show_search(client, query, key)


@Client.on_callback_query(filters.regex(r"^mback#"))
async def mymovies_back_titles_cb(client, query):
    key = query.data.split("#", 1)[1]
    await _pro_show_search(client, query, key)



@Client.on_message(filters.group & filters.text & filters.incoming)
async def give_filter(client, message):
    if EMOJI_MODE:
        try:
            await message.react(emoji=random.choice(REACTIONS), big=True)
        except Exception:
            await message.react(emoji="⚡️", big=True)
    await mdb.update_top_messages(message.from_user.id, message.text)
    if message.chat.id != SUPPORT_CHAT_ID:
        settings = await get_settings(message.chat.id)
        try:
            if settings['auto_ffilter']:
                if re.search(r'https?://\S+|www\.\S+|t\.me/\S+', message.text):
                    if await is_check_admin(client, message.chat.id, message.from_user.id):
                        return
                    return await message.delete()
                await auto_filter(client, message)
        except KeyError:
            pass
    else:
        search = message.text
        _, _, total_results = await get_search_results(chat_id=message.chat.id, query=search.lower(), offset=0, filter=True)
        if total_results == 0:
            return
        await message.reply_text(
            f"<b>Hᴇʏ {message.from_user.mention},\n\n"
            f"ʏᴏᴜʀ ʀᴇǫᴜᴇꜱᴛ ɪꜱ ᴀʟʀᴇᴀᴅʏ ᴀᴠᴀɪʟᴀʙʟᴇ ✅\n\n"
            f"📂 ꜰɪʟᴇꜱ ꜰᴏᴜɴᴅ : {str(total_results)}\n"
            f"🔍 ꜱᴇᴀʀᴄʜ :</b> <code>{search}</code>\n\n"
            f"<b>‼️ ᴛʜɪs ɪs ᴀ <u>sᴜᴘᴘᴏʀᴛ ɢʀᴏᴜᴘ</u> sᴏ ᴛʜᴀᴛ ʏᴏᴜ ᴄᴀɴ'ᴛ ɢᴇᴛ ғɪʟᴇs ғʀᴏᴍ ʜᴇʀᴇ...\n\n"
            f"📝 ꜱᴇᴀʀᴄʜ ʜᴇʀᴇ : 👇</b>",
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("🔍 ᴊᴏɪɴ ᴀɴᴅ ꜱᴇᴀʀᴄʜ ʜᴇʀᴇ 🔎", url=GRP_LNK)]])
        )


@Client.on_message(filters.private & filters.text & filters.incoming & ~filters.regex(r"^/"))
async def pm_text(bot, message):
    bot_id = bot.me.id
    content = message.text
    user = message.from_user.first_name
    user_id = message.from_user.id
    if EMOJI_MODE:
        try:
            await message.react(emoji=random.choice(REACTIONS), big=True)
        except Exception:
            await message.react(emoji="⚡️", big=True)
    if content.startswith(("#")):
        return
    try:
        await mdb.update_top_messages(user_id, content)
        pm_search = await db.pm_search_status(bot_id)
        if pm_search:
            await auto_filter(bot, message)
        else:
            await message.reply_text(
                text=(
                    f"<b>🙋 ʜᴇʏ {user} 😍 ,\n\n"
                    "𝒀𝒐𝒖 𝒄𝒂𝒏 𝒔𝒆𝒂𝒓𝒄𝒉 𝒇𝒐𝒓 𝒎𝒐𝒗𝒊𝒆𝒔 𝒐𝒏𝒍𝒚 𝒐𝒏 𝒐𝒖𝒓 𝑴𝒐𝒗𝒊𝒆 𝑮𝒓𝒐𝒖𝒑. 𝒀𝒐𝒖 𝒂𝒓𝒆 𝒏𝒐𝒕 𝒂𝒍𝒍𝒐𝒘𝒆𝒅 𝒕𝒐 𝒔𝒆𝒂𝒓𝒄𝒉 𝒇𝒐𝒓 𝒎𝒐𝒗𝒊𝒆𝒔 𝒐𝒏 𝑫𝒊𝒓𝒆𝒄𝒕 𝑩𝒐𝒕. 𝑷𝒍𝒆𝒂𝒔𝒆 𝒋𝒐𝒊𝒏 𝒐𝒖𝒓 𝒎𝒐𝒗𝒊𝒆 𝒈𝒓𝒐𝒖𝒑 𝒃𝒚 𝒄𝒍𝒊𝒄𝒌𝒊𝒏𝒈 𝒐𝒏 𝒕𝒉𝒆  𝑹𝑬𝑸𝑼𝑬𝑺𝑻 𝑯𝑬𝑹𝑬 𝒃𝒖𝒕𝒕𝒐𝒏 𝒈𝒊𝒗𝒆𝒏 𝒃𝒆𝒍𝒐𝒘 𝒂𝒏𝒅 𝒔𝒆𝒂𝒓𝒄𝒉 𝒚𝒐𝒖𝒓 𝒇𝒂𝒗𝒐𝒓𝒊𝒕𝒆 𝒎𝒐𝒗𝒊𝒆 𝒕𝒉𝒆𝒓𝒆 👇\n\n"
                    "<blockquote>"
                    "आप केवल हमारे 𝑴𝒐𝒗𝒊𝒆 𝑮𝒓𝒐𝒖𝒑 पर ही 𝑴𝒐𝒗𝒊𝒆 𝑺𝒆𝒂𝒓𝒄𝒉 कर सकते हो । "
                    "आपको 𝑫𝒊𝒓𝒆𝒄𝒕 𝑩𝒐𝒕 पर 𝑴𝒐𝒗𝒊𝒆 𝑺𝒆𝒂𝒓𝒄𝒉 करने की 𝑷𝒆𝒓𝒎𝒊𝒔𝒔𝒊𝒐𝒏 नहीं है कृपया नीचे दिए गए 𝑹𝑬𝑸𝑼𝑬𝑺𝑻 𝑯𝑬𝑹𝑬 वाले 𝑩𝒖𝒕𝒕𝒐𝒏 पर क्लिक करके हमारे 𝑴𝒐𝒗𝒊𝒆 𝑮𝒓𝒐𝒖𝒑 को 𝑱𝒐𝒊𝒏 करें और वहां पर अपनी मनपसंद 𝑴𝒐𝒗𝒊𝒆 𝑺𝒆𝒂𝒓𝒄𝒉 सर्च करें ।"
                    "</blockquote></b>"
                ), reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("📝 ʀᴇǫᴜᴇsᴛ ʜᴇʀᴇ ", url=GRP_LNK)]]))
            await bot.send_message(chat_id=LOG_CHANNEL,
                                   text=(
                                       f"<b>#𝐏𝐌_𝐌𝐒𝐆\n\n"
                                       f"👤 Nᴀᴍᴇ : {user}\n"
                                       f"🆔 ID : {user_id}\n"
                                       f"💬 Mᴇssᴀɢᴇ : {content}</b>"
                                   )
                                   )
    except Exception:
        pass


@Client.on_callback_query(filters.regex(r"^reffff"))
async def refercall(bot, query):
    btn = [[
        InlineKeyboardButton(
            'invite link', url=f'https://telegram.me/share/url?url=https://t.me/{bot.me.username}?start=reff_{query.from_user.id}&text=Hello%21%20Experience%20a%20bot%20that%20offers%20a%20vast%20library%20of%20unlimited%20movies%20and%20series.%20%F0%9F%98%83'),
        InlineKeyboardButton(
            f'⏳ {referdb.get_refer_points(query.from_user.id)}', callback_data='ref_point'),
        InlineKeyboardButton('Back', callback_data='premium_info')
    ]]
    reply_markup = InlineKeyboardMarkup(btn)
    try:
        await bot.edit_message_media(
            query.message.chat.id,
            query.message.id,
            InputMediaPhoto("https://graph.org/file/1a2e64aee3d4d10edd930.jpg")
        )
    except Exception as e:    
        pass
    await query.message.edit_text(
        text=f'Hay Your refer link:\n\nhttps://t.me/{bot.me.username}?start=reff_{query.from_user.id}\n\nShare this link with your friends, Each time they join,  you will get 10 refferal points and after 100 points you will get 1 month premium subscription.',
        reply_markup=reply_markup,
        parse_mode=enums.ParseMode.HTML
    )
    await query.answer()

@Client.on_callback_query(filters.regex(r"^mfileprev#"))
async def pro_movie_files_previous_page(bot, query):
    key = query.data.split("#", 1)[1]
    if key not in PRO_DETAIL:
        return await query.answer("⚠️ This movie page has expired.", show_alert=True)
    state = PRO_DETAIL[key]
    history = state.get("history", [0])
    if len(history) <= 1:
        return await query.answer("Already on the first file page.")
    history.pop()
    previous_offset = history[-1]
    return await _pro_render_detail(bot, query, key, previous_offset, push_history=False)


@Client.on_callback_query(filters.regex(r"^mfilenext#"))
async def pro_movie_files_next_page(bot, query):
    """Phase-3-only file pagination; never enters the legacy renderer."""
    try:
        _, key, raw_offset = query.data.split("#", 2)
        offset = int(raw_offset)
    except (ValueError, TypeError):
        return await query.answer("⚠️ Invalid page.", show_alert=True)
    if key not in PRO_DETAIL:
        return await query.answer("⚠️ This movie page has expired.", show_alert=True)
    state = PRO_DETAIL[key]
    if state.get("user_id") not in (None, 0, query.from_user.id):
        return await query.answer("⚠️ This is not your movie request.", show_alert=True)
    return await _pro_render_detail(bot, query, key, offset, push_history=True)


@Client.on_callback_query(filters.regex(r"^next"))
async def next_page(bot, query):
    ident, req, key, offset = query.data.split("_")
    # Hard guard: even if Pyrogram dispatch order changes, PRO movie pages
    # must never fall back to the legacy raw-file renderer.
    if key in PRO_DETAIL:
        if int(req) not in [query.from_user.id, 0]:
            return await query.answer(script.ALRT_TXT.format(query.from_user.first_name), show_alert=True)
        try:
            offset_int = int(offset)
        except (TypeError, ValueError):
            offset_int = 0
        return await _pro_render_detail(bot, query, key, offset_int, push_history=True)
    curr_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
    if int(req) not in [query.from_user.id, 0]:
        return await query.answer(script.ALRT_TXT.format(query.from_user.first_name), show_alert=True)
    try:
        offset = int(offset)
    except:
        offset = 0
    if BUTTONS.get(key) != None:
        search = BUTTONS.get(key)
    else:
        search = FRESH.get(key)
    if not search:
        await query.answer(script.OLD_ALRT_TXT.format(query.from_user.first_name), show_alert=True)
        return
    files, n_offset, total = await get_search_results(query.message.chat.id, search, offset=offset, filter=True)
    try:
        n_offset = int(n_offset)
    except:
        n_offset = 0

    if not files:
        return
    temp.GETALL[key] = files
    temp.SHORT[query.from_user.id] = query.message.chat.id
    settings = await get_settings(query.message.chat.id)
    if settings.get('button'):
        btn = [
            [
                InlineKeyboardButton(text=_pro_file_btn(file), callback_data=f'file#{file.file_id}'),
            ]
            for file in files
        ]
        btn.insert(0,
                   [
                       InlineKeyboardButton(
                           "✦ Quality", callback_data=f"qualities#{key}"),
                       InlineKeyboardButton(
                           "✦ Language", callback_data=f"languages#{key}"),
                       InlineKeyboardButton(
                           "✦ Season",  callback_data=f"seasons#{key}")
                   ]
                   )
        btn.insert(0,
                   [
                       InlineKeyboardButton(
                           "✦ Premium", url=f"https://t.me/{temp.U_NAME}?start=premium"),
                       InlineKeyboardButton(
                           "✦ Send All", callback_data=f"sendfiles#{key}")

                   ]
                   )

    else:
        btn = []
        btn.insert(0,
                   [
                       InlineKeyboardButton(
                           "✦ Quality", callback_data=f"qualities#{key}"),
                       InlineKeyboardButton(
                           "✦ Language", callback_data=f"languages#{key}"),
                       InlineKeyboardButton(
                           "✦ Season",  callback_data=f"seasons#{key}")
                   ]
                   )
        btn.insert(0, [
            InlineKeyboardButton(
                "✦ Premium", url=f"https://t.me/{temp.U_NAME}?start=premium"),
            InlineKeyboardButton("✦ Send All", callback_data=f"sendfiles#{key}")
        ])
    if ULTRA_FAST_MODE:
        if 0 < offset <= 10:
            off_set = 0
        elif offset == 0:
            off_set = None
        else:
            off_set = offset - 10
        if n_offset == 0:
            btn.append(
                [InlineKeyboardButton("‹ Back", callback_data=f"next_{req}_{key}_{off_set}"), InlineKeyboardButton(f"{math.ceil(int(offset)/10)+1}", callback_data="pages")]
            )
        elif off_set is None:
            btn.append([InlineKeyboardButton("Page", callback_data="pages"), InlineKeyboardButton(f"{math.ceil(int(offset)/10)+1}", callback_data="pages"), InlineKeyboardButton(_pro_label("➜", "Next"), callback_data=f"next_{req}_{key}_{n_offset}")])
        else:
            btn.append(
                [
                    InlineKeyboardButton("‹ Back", callback_data=f"next_{req}_{key}_{off_set}"),
                    InlineKeyboardButton(f"{math.ceil(int(offset)/10)+1}", callback_data="pages"),
                    InlineKeyboardButton(_pro_label("➜", "Next"), callback_data=f"next_{req}_{key}_{n_offset}")
                ],
            )
    else:
        try:
            if settings['max_btn']:
                if 0 < offset <= 10:
                    off_set = 0
                elif offset == 0:
                    off_set = None
                else:
                    off_set = offset - 10
                if n_offset == 0:
                    btn.append([InlineKeyboardButton("‹ Back", callback_data=f"next_{req}_{key}_{off_set}"), InlineKeyboardButton(
                        f"{math.ceil(int(offset)/10)+1} / {math.ceil(total/10)}", callback_data="pages")])
                elif off_set is None:
                    btn.append([InlineKeyboardButton("Page", callback_data="pages"), InlineKeyboardButton(
                        f"{math.ceil(int(offset)/10)+1} / {math.ceil(total/10)}", callback_data="pages"), InlineKeyboardButton(_pro_label("➜", "Next"), callback_data=f"next_{req}_{key}_{n_offset}")])
                else:
                    btn.append(
                        [
                            InlineKeyboardButton(
                                "‹ Back", callback_data=f"next_{req}_{key}_{off_set}"),
                            InlineKeyboardButton(
                                f"{math.ceil(int(offset)/10)+1} / {math.ceil(total/10)}", callback_data="pages"),
                            InlineKeyboardButton(
                                "Next ›", callback_data=f"next_{req}_{key}_{n_offset}")
                        ],
                    )
            else:
                if 0 < offset <= int(MAX_B_TN):
                    off_set = 0
                elif offset == 0:
                    off_set = None
                else:
                    off_set = offset - int(MAX_B_TN)
                if n_offset == 0:
                    btn.append([InlineKeyboardButton("‹ Back", callback_data=f"next_{req}_{key}_{off_set}"), InlineKeyboardButton(
                        f"{math.ceil(int(offset)/int(MAX_B_TN))+1} / {math.ceil(total/int(MAX_B_TN))}", callback_data="pages")])
                elif off_set is None:
                    btn.append([InlineKeyboardButton("Page", callback_data="pages"), InlineKeyboardButton(
                        f"{math.ceil(int(offset)/int(MAX_B_TN))+1} / {math.ceil(total/int(MAX_B_TN))}", callback_data="pages"), InlineKeyboardButton(_pro_label("➜", "Next"), callback_data=f"next_{req}_{key}_{n_offset}")])
                else:
                    btn.append(
                        [
                            InlineKeyboardButton(
                                "‹ Back", callback_data=f"next_{req}_{key}_{off_set}"),
                            InlineKeyboardButton(
                                f"{math.ceil(int(offset)/int(MAX_B_TN))+1} / {math.ceil(total/int(MAX_B_TN))}", callback_data="pages"),
                            InlineKeyboardButton(
                                "Next ›", callback_data=f"next_{req}_{key}_{n_offset}")
                        ],
                    )
        except KeyError:
            await save_group_settings(query.message.chat.id, 'max_btn', True)
            if 0 < offset <= 10:
                off_set = 0
            elif offset == 0:
                off_set = None
            else:
                off_set = offset - 10
            if n_offset == 0:
                btn.append(
                    [InlineKeyboardButton("‹ Back", callback_data=f"next_{req}_{key}_{off_set}"), InlineKeyboardButton(
                        f"{math.ceil(int(offset)/10)+1} / {math.ceil(total/10)}", callback_data="pages")]
                )
            elif off_set is None:
                btn.append([InlineKeyboardButton("Page", callback_data="pages"), InlineKeyboardButton(
                    f"{math.ceil(int(offset)/10)+1} / {math.ceil(total/10)}", callback_data="pages"), InlineKeyboardButton(_pro_label("➜", "Next"), callback_data=f"next_{req}_{key}_{n_offset}")])
            else:
                btn.append(
                    [
                        InlineKeyboardButton(
                            "‹ Back", callback_data=f"next_{req}_{key}_{off_set}"),
                        InlineKeyboardButton(
                            f"{math.ceil(int(offset)/10)+1} / {math.ceil(total/10)}", callback_data="pages"),
                        InlineKeyboardButton(
                            "Next ›", callback_data=f"next_{req}_{key}_{n_offset}")
                    ],
                )
    if not settings["button"]:
        cur_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
        time_difference = timedelta(hours=cur_time.hour, minutes=cur_time.minute, seconds=(cur_time.second+(cur_time.microsecond/1000000))) - \
            timedelta(hours=curr_time.hour, minutes=curr_time.minute, seconds=(
                curr_time.second+(curr_time.microsecond/1000000)))
        remaining_seconds = "{:.2f}".format(time_difference.total_seconds())
        dreamx_title = clean_search_text(search)
        cap = None
        try:
            if settings['imdb']:
                cap = await get_cap(settings, remaining_seconds, files, query, total, dreamx_title, offset)
                if query.message.caption:
                    try:
                        await query.message.edit_caption(caption=cap, reply_markup=btn if isinstance(btn, InlineKeyboardMarkup) else InlineKeyboardMarkup(btn), parse_mode=enums.ParseMode.HTML)
                    except Exception as e:
                        logger.exception(e)
                        await query.message.edit_text(text=cap, reply_markup=btn if isinstance(btn, InlineKeyboardMarkup) else InlineKeyboardMarkup(btn), disable_web_page_preview=True, parse_mode=enums.ParseMode.HTML)
                else:
                    await query.message.edit_text(text=cap, reply_markup=btn if isinstance(btn, InlineKeyboardMarkup) else InlineKeyboardMarkup(btn), disable_web_page_preview=True, parse_mode=enums.ParseMode.HTML)
            else:
                cap = await get_cap(settings, remaining_seconds, files, query, total, dreamx_title, offset+1)
                await query.message.edit_text(text=cap, reply_markup=btn if isinstance(btn, InlineKeyboardMarkup) else InlineKeyboardMarkup(btn), disable_web_page_preview=True, parse_mode=enums.ParseMode.HTML)
        except Exception as e:

            logger.exception("Failed to send result: %s", e)
        except MessageNotModified:
            pass
        # try:
        #     await query.message.edit_text(text=cap, reply_markup=btn if isinstance(btn, InlineKeyboardMarkup) else InlineKeyboardMarkup(btn), disable_web_page_preview=True, parse_mode=enums.ParseMode.HTML)
        # except MessageNotModified:
        #     pass
    else:
        try:
            btn = _pro_add_back_to_titles(btn, key)
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(btn))
        except MessageNotModified:
            pass
    await query.answer()


@Client.on_callback_query(filters.regex(r"^spol"))
async def advantage_spoll_choker(bot, query):
    _, id, user = query.data.split('#')
    if int(user) != 0 and query.from_user.id != int(user):
        return await query.answer(script.ALRT_TXT.format(query.from_user.first_name), show_alert=True)
    movies = await get_posterx(id, id=True) if TMDB_ON_SEARCH else await get_poster(id, id=True)
    movie = movies.get('title')
    movie = re.sub(r"[:-]", " ", movie)
    movie = re.sub(r"\s+", " ", movie).strip()
    await query.answer(script.TOP_ALRT_MSG)
    files, offset, total_results = await get_search_results(query.message.chat.id, movie, offset=0, filter=True)
    if files:
        k = (movie, files, offset, total_results)
        await auto_filter(bot, query, k)
    else:
        reqstr1 = query.from_user.id if query.from_user else 0
        reqstr = await bot.get_users(reqstr1)
        if NO_RESULTS_MSG:
            try:
                await bot.send_message(chat_id=BIN_CHANNEL, text=script.NORSLTS.format(reqstr.id, reqstr.mention, movie))
            except Exception as e:
                print(f"Error In Spol - {e}   Make Sure Bot Admin BIN CHANNEL")
        btn = InlineKeyboardMarkup(
            [[InlineKeyboardButton("🔰Cʟɪᴄᴋ ʜᴇʀᴇ & ʀᴇǫᴜᴇsᴛ ᴛᴏ ᴀᴅᴍɪɴ🔰", url=OWNER_LNK)]])
        k = await query.message.edit(script.MVE_NT_FND, reply_markup=btn)
        await asyncio.sleep(10)
        await k.delete()

# Qualities
@Client.on_callback_query(filters.regex(r"^qualities#"))
async def qualities_cb_handler(client: Client, query: CallbackQuery):
    try:
        if int(query.from_user.id) not in [query.message.reply_to_message.from_user.id, 0]:
            return await query.answer(
                f"⚠️ ʜᴇʟʟᴏ {query.from_user.first_name},\n"
                f"ᴛʜɪꜱ ɪꜱ ɴᴏᴛ ʏᴏᴜʀ ᴍᴏᴠɪᴇ ʀᴇǫᴜᴇꜱᴛ,\nʀᴇǫᴜᴇꜱᴛ ʏᴏᴜʀ'ꜱ...",
                show_alert=True,
            )
    except:
        pass

    _, key = query.data.split("#")
    search = FRESH.get(key)
    search = search.replace(' ', '_')

    btn = []
    for i in range(0, len(QUALITIES), 2):
        q1 = QUALITIES[i]
        row = [InlineKeyboardButton(
            text=q1, callback_data=f"fq#{q1.lower()}#{key}")]
        if i + 1 < len(QUALITIES):
            q2 = QUALITIES[i + 1]
            row.append(InlineKeyboardButton(
                text=q2, callback_data=f"fq#{q2.lower()}#{key}"))
        btn.append(row)

    btn.insert(0, [
        InlineKeyboardButton(text="⇊ ꜱᴇʟᴇᴄᴛ ǫᴜᴀʟɪᴛʏ ⇊", callback_data="ident")
    ])
    btn.append([
        InlineKeyboardButton(text="↭ ʙᴀᴄᴋ ᴛᴏ ꜰɪʟᴇs ↭",
                             callback_data=f"fq#homepage#{key}")
    ])

    btn = _pro_add_back_to_titles(btn, key)
    await query.edit_message_reply_markup(InlineKeyboardMarkup(btn))


@Client.on_callback_query(filters.regex(r"^fq#"))
async def filter_qualities_cb_handler(client: Client, query: CallbackQuery):
    _, qual, key = query.data.split("#")
    curr_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
    search = FRESH.get(key)
    search = search.replace("_", " ")
    baal = qual in search
    if baal:
        search = search.replace(qual, "")
    else:
        search = search
    req = query.from_user.id
    chat_id = query.message.chat.id
    message = query.message
    try:
        if int(query.from_user.id) not in [query.message.reply_to_message.from_user.id, 0]:
            return await query.answer(f"⚠️ ʜᴇʟʟᴏ {query.from_user.first_name},\nᴛʜɪꜱ ɪꜱ ɴᴏᴛ ʏᴏᴜʀ ᴍᴏᴠɪᴇ ʀᴇǫᴜᴇꜱᴛ,\nʀᴇǫᴜᴇꜱᴛ ʏᴏᴜʀ'ꜱ...", show_alert=True,)
    except:
        pass
    if qual != "homepage":
        search = f"{search} {qual}"
    BUTTONS[key] = search
    files, offset, total_results = await get_search_results(chat_id, search, offset=0, filter=True)
    if not files:
        await query.answer("🚫 ɴᴏ ꜰɪʟᴇꜱ ᴡᴇʀᴇ ꜰᴏᴜɴᴅ 🚫", show_alert=1)
        return
    temp.GETALL[key] = files
    settings = await get_settings(message.chat.id)
    if settings.get('button'):
        btn = [
            [
                InlineKeyboardButton(text=_pro_file_btn(file), callback_data=f'file#{file.file_id}'),
            ]
            for file in files
        ]
        btn.insert(0,
                   [
                       InlineKeyboardButton(
                           "✦ Quality", callback_data=f"qualities#{key}"),
                       InlineKeyboardButton(
                           "✦ Language", callback_data=f"languages#{key}"),
                       InlineKeyboardButton(
                           "✦ Season",  callback_data=f"seasons#{key}")
                   ]
                   )
        btn.insert(0,
                   [
                       InlineKeyboardButton(
                           "✦ Premium", url=f"https://t.me/{temp.U_NAME}?start=premium"),
                       InlineKeyboardButton(
                           "✦ Send All", callback_data=f"sendfiles#{key}")
                   ])
    else:
        btn = []
        btn.insert(0,
                   [
                       InlineKeyboardButton(
                           "✦ Quality", callback_data=f"qualities#{key}"),
                       InlineKeyboardButton(
                           "✦ Language", callback_data=f"languages#{key}"),
                       InlineKeyboardButton(
                           "✦ Season",  callback_data=f"seasons#{key}")
                   ]
                   )
        btn.insert(0,
                   [
                       InlineKeyboardButton(
                           "✦ Premium", url=f"https://t.me/{temp.U_NAME}?start=premium"),
                       InlineKeyboardButton(
                           "✦ Send All", callback_data=f"sendfiles#{key}")

                   ])
    if offset != "":
        try:
            if settings['max_btn']:
                btn.append(

                    [InlineKeyboardButton("Page", callback_data="pages"), InlineKeyboardButton(
                        text=f"1/{math.ceil(int(total_results)/10)}", callback_data="pages"), InlineKeyboardButton(text="Next ›", callback_data=f"next_{req}_{key}_{offset}")]
                )
            else:
                btn.append(

                    [InlineKeyboardButton("Page", callback_data="pages"), InlineKeyboardButton(
                        text=f"1/{math.ceil(int(total_results)/int(MAX_B_TN))}", callback_data="pages"), InlineKeyboardButton(text="Next ›", callback_data=f"next_{req}_{key}_{offset}")]
                )
        except KeyError:
            await save_group_settings(query.message.chat.id, 'max_btn', True)
            btn.append(

                [InlineKeyboardButton("Page", callback_data="pages"), InlineKeyboardButton(
                    text=f"1/{math.ceil(int(total_results)/10)}", callback_data="pages"), InlineKeyboardButton(text="Next ›", callback_data=f"next_{req}_{key}_{offset}")]
            )
    else:
        btn.append(

            [InlineKeyboardButton(
                text="↭ ɴᴏ ᴍᴏʀᴇ ᴘᴀɢᴇꜱ ᴀᴠᴀɪʟᴀʙʟᴇ ↭", callback_data="pages")]
        )
    if not settings["button"]:
        cur_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
        time_difference = timedelta(hours=cur_time.hour, minutes=cur_time.minute, seconds=(cur_time.second+(cur_time.microsecond/1000000))) - \
            timedelta(hours=curr_time.hour, minutes=curr_time.minute, seconds=(
                curr_time.second+(curr_time.microsecond/1000000)))
        remaining_seconds = "{:.2f}".format(time_difference.total_seconds())
        dreamx_title = clean_search_text(search)
        cap = await get_cap(settings, remaining_seconds, files, query, total_results, dreamx_title, offset=1)
        try:
            await query.message.edit_text(text=cap, reply_markup=btn if isinstance(btn, InlineKeyboardMarkup) else InlineKeyboardMarkup(btn), disable_web_page_preview=True, parse_mode=enums.ParseMode.HTML)
        except MessageNotModified:
            pass
    else:
        try:
            btn = _pro_add_back_to_titles(btn, key)
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(btn))
        except MessageNotModified:
            pass
    await query.answer()

# languages


@Client.on_callback_query(filters.regex(r"^languages#"))
async def languages_cb_handler(client: Client, query: CallbackQuery):
    try:
        if int(query.from_user.id) not in [query.message.reply_to_message.from_user.id, 0]:
            return await query.answer(
                f"⚠️ ʜᴇʟʟᴏ {query.from_user.first_name},\n"
                f"ᴛʜɪꜱ ɪꜱ ɴᴏᴛ ʏᴏᴜʀ ᴍᴏᴠɪᴇ ʀᴇǫᴜᴇꜱᴛ,\nʀᴇǫᴜᴇꜱᴛ ʏᴏᴜʀ'ꜱ...",
                show_alert=True,
            )
    except:
        pass

    _, key = query.data.split("#")
    search = FRESH.get(key)
    search = search.replace(' ', '_')

    items = list(LANGUAGES.items())
    btn = []

    for i in range(0, len(items), 2):
        name1, code1 = items[i]
        row = [InlineKeyboardButton(
            text=name1, callback_data=f"fl#{code1}#{key}")]
        if i + 1 < len(items):
            name2, code2 = items[i + 1]
            row.append(InlineKeyboardButton(
                text=name2, callback_data=f"fl#{code2}#{key}"))
        btn.append(row)

    btn.insert(0, [InlineKeyboardButton(
        text="⇊ ꜱᴇʟᴇᴄᴛ ʟᴀɴɢᴜᴀɢᴇ ⇊", callback_data="ident")])
    btn.append([InlineKeyboardButton(text="↭ ʙᴀᴄᴋ ᴛᴏ ꜰɪʟᴇs ↭",
               callback_data=f"fl#homepage#{key}")])

    btn = _pro_add_back_to_titles(btn, key)
    await query.edit_message_reply_markup(InlineKeyboardMarkup(btn))


@Client.on_callback_query(filters.regex(r"^fl#"))
async def filter_languages_cb_handler(client: Client, query: CallbackQuery):
    _, lang, key = query.data.split("#")
    curr_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
    search = FRESH.get(key)
    search = search.replace("_", " ")
    baal = lang in search
    if baal:
        search = search.replace(lang, "")
    else:
        search = search
    req = query.from_user.id
    chat_id = query.message.chat.id
    message = query.message
    try:
        if int(query.from_user.id) not in [query.message.reply_to_message.from_user.id, 0]:
            return await query.answer(f"⚠️ ʜᴇʟʟᴏ {query.from_user.first_name},\nᴛʜɪꜱ ɪꜱ ɴᴏᴛ ʏᴏᴜʀ ᴍᴏᴠɪᴇ ʀᴇǫᴜᴇꜱᴛ,\nʀᴇǫᴜᴇꜱᴛ ʏᴏᴜʀ'ꜱ...", show_alert=True,)
    except:
        pass
    if lang != "homepage":
        search = f"{search} {lang}"
    BUTTONS[key] = search
    files, offset, total_results = await get_search_results(chat_id, search, offset=0, filter=True)
    if not files:
        await query.answer("🚫 ɴᴏ ꜰɪʟᴇꜱ ᴡᴇʀᴇ ꜰᴏᴜɴᴅ 🚫", show_alert=1)
        return
    temp.GETALL[key] = files
    settings = await get_settings(message.chat.id)
    if settings.get('button'):
        btn = [
            [
                InlineKeyboardButton(text=_pro_file_btn(file), callback_data=f'file#{file.file_id}'),
            ]
            for file in files
        ]
        btn.insert(0,
                   [
                       InlineKeyboardButton(
                           "✦ Quality", callback_data=f"qualities#{key}"),
                       InlineKeyboardButton(
                           "✦ Language", callback_data=f"languages#{key}"),
                       InlineKeyboardButton(
                           "✦ Season",  callback_data=f"seasons#{key}")
                   ]
                   )
        btn.insert(0,
                   [
                       InlineKeyboardButton(
                           "✦ Premium", url=f"https://t.me/{temp.U_NAME}?start=premium"),
                       InlineKeyboardButton(
                           "✦ Send All", callback_data=f"sendfiles#{key}")
                   ]
                   )
    else:
        btn = []
        btn.insert(0,
                   [
                       InlineKeyboardButton(
                           "✦ Quality", callback_data=f"qualities#{key}"),
                       InlineKeyboardButton(
                           "✦ Language", callback_data=f"languages#{key}"),
                       InlineKeyboardButton(
                           "✦ Season",  callback_data=f"seasons#{key}")
                   ])
        btn.insert(0,
                   [
                       InlineKeyboardButton(
                           "✦ Premium", url=f"https://t.me/{temp.U_NAME}?start=premium"),
                       InlineKeyboardButton(
                           "✦ Send All", callback_data=f"sendfiles#{key}")
                   ])
    if offset != "":
        try:
            if settings['max_btn']:
                btn.append(
                    [
                        InlineKeyboardButton("Page", callback_data="pages"), InlineKeyboardButton(
                            text=f"1/{math.ceil(int(total_results)/10)}", callback_data="pages"), InlineKeyboardButton(text="Next ›", callback_data=f"next_{req}_{key}_{offset}")
                    ])
            else:
                btn.append(
                    [
                        InlineKeyboardButton("Page", callback_data="pages"), InlineKeyboardButton(
                            text=f"1/{math.ceil(int(total_results)/int(MAX_B_TN))}", callback_data="pages"), InlineKeyboardButton(text="Next ›", callback_data=f"next_{req}_{key}_{offset}")
                    ])
        except KeyError:
            await save_group_settings(query.message.chat.id, 'max_btn', True)
            btn.append(
                [
                    InlineKeyboardButton("Page", callback_data="pages"), InlineKeyboardButton(
                        text=f"1/{math.ceil(int(total_results)/10)}", callback_data="pages"), InlineKeyboardButton(text="Next ›", callback_data=f"next_{req}_{key}_{offset}")
                ])
    else:
        btn.append([InlineKeyboardButton(
            text="↭ ɴᴏ ᴍᴏʀᴇ ᴘᴀɢᴇꜱ ᴀᴠᴀɪʟᴀʙʟᴇ ↭", callback_data="pages")])
    if not settings["button"]:
        cur_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
        time_difference = timedelta(hours=cur_time.hour, minutes=cur_time.minute, seconds=(cur_time.second+(cur_time.microsecond/1000000))) - \
            timedelta(hours=curr_time.hour, minutes=curr_time.minute, seconds=(
                curr_time.second+(curr_time.microsecond/1000000)))
        remaining_seconds = "{:.2f}".format(time_difference.total_seconds())
        dreamx_title = clean_search_text(search)
        cap = await get_cap(settings, remaining_seconds, files, query, total_results, dreamx_title, offset=1)
        try:
            await query.message.edit_text(text=cap, reply_markup=btn if isinstance(btn, InlineKeyboardMarkup) else InlineKeyboardMarkup(btn), disable_web_page_preview=True, parse_mode=enums.ParseMode.HTML)
        except MessageNotModified:
            pass
    else:
        try:
            btn = _pro_add_back_to_titles(btn, key)
            await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(btn))
        except MessageNotModified:
            pass
    await query.answer()


@Client.on_callback_query(filters.regex(r"^seasons#"))
async def seasons_cb_handler(client: Client, query: CallbackQuery):
    try:
        if int(query.from_user.id) not in [query.message.reply_to_message.from_user.id, 0]:
            return await query.answer(
                f"⚠️ ʜᴇʟʟᴏ {query.from_user.first_name},\nᴛʜɪꜱ ɪꜱ ɴᴏᴛ ʏᴏᴜʀ ᴍᴏᴠɪᴇ ʀᴇǫᴜᴇꜱᴛ,\nʀᴇǫᴜᴇꜱᴛ ʏᴏᴜʀ'ꜱ…",
                show_alert=True,
            )
    except Exception:
        pass
    _, key = query.data.split("#")

    req = query.from_user.id

    # Phase 3: on a PRO movie page, Season is a real detail-state selector.
    # It does not reuse the legacy reply-markup-only flow because that would
    # leave the old poster and title on screen.
    if key in PRO_DETAIL:
        btn: list[list[InlineKeyboardButton]] = [[
            InlineKeyboardButton("⇊ SELECT SEASON ⇊", callback_data="ident")
        ]]
        season_numbers = []
        for raw in SEASONS:
            try:
                season_numbers.append(int(str(raw).lstrip("S")))
            except Exception:
                continue
        for i in range(0, len(season_numbers), 2):
            row = [InlineKeyboardButton(
                f"📺 Season {season_numbers[i]}",
                callback_data=f"fs#s{season_numbers[i]:02d}#{key}"
            )]
            if i + 1 < len(season_numbers):
                row.append(InlineKeyboardButton(
                    f"📺 Season {season_numbers[i+1]}",
                    callback_data=f"fs#s{season_numbers[i+1]:02d}#{key}"
                ))
            btn.append(row)
        btn.append([InlineKeyboardButton("↩ Back to Files", callback_data=f"mfilenext#{key}#0")])
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(btn))
        return await query.answer()

    search = FRESH.get(key).replace(" ", "_")
    offset = 0
    btn: list[list[InlineKeyboardButton]] = []
    for i in range(0, len(SEASONS) - 1, 2):
        btn.append([
            InlineKeyboardButton(
                f"Sᴇᴀꜱᴏɴ {SEASONS[i][1:]}", callback_data=f"fs#{SEASONS[i].lower()}#{key}"),
            InlineKeyboardButton(
                f"Sᴇᴀꜱᴏɴ {SEASONS[i+1][1:]}", callback_data=f"fs#{SEASONS[i+1].lower()}#{key}")
        ])

    btn.insert(
        0,
        [InlineKeyboardButton("⇊ ꜱᴇʟᴇᴄᴛ ꜱᴇᴀꜱᴏɴ ⇊", callback_data="ident")],
    )
    btn.append([InlineKeyboardButton(text="↭ ʙᴀᴄᴋ ᴛᴏ ꜰɪʟᴇs ​↭",
               callback_data=f"next_{req}_{key}_{offset}")])
    btn = _pro_add_back_to_titles(btn, key)
    await query.edit_message_reply_markup(InlineKeyboardMarkup(btn))
    await query.answer()


@Client.on_callback_query(filters.regex(r"^fs#"))
async def filter_seasons_cb_handler(client: Client, query: CallbackQuery):
    _, season_tag, key = query.data.split("#")
    season_tag = season_tag.lower()

    # Phase 3: season selection on a movie detail page. Fetch only the selected
    # season's files and swap to a season-specific TMDB poster when available.
    if key in PRO_DETAIL:
        state = PRO_DETAIL[key]
        base_title = state.get("title") or FRESH.get(key) or "Movie"
        if season_tag == "homepage":
            state["season_number"] = None
            state["active_query"] = base_title
            state["meta"] = state.get("base_meta") or state.get("meta") or {}
        else:
            try:
                season_number = int(re.search(r"\d+", season_tag).group(0))
            except Exception:
                return await query.answer("⚠️ Invalid season.", show_alert=True)
            variations = generate_season_variations(base_title, season_number)
            files = []
            chosen_query = variations[0] if variations else f"{base_title} s{season_number:02d}"
            for candidate in variations:
                found, _, _ = await get_search_results(query.message.chat.id, candidate, offset=0, filter=True)
                if found:
                    files = found
                    chosen_query = candidate
                    break
            if not files:
                return await query.answer("🚫 No files found for this season.", show_alert=True)
            state["season_number"] = season_number
            state["active_query"] = chosen_query
            state["history"] = [0]
            state["meta"] = await _pro_get_season_meta(base_title, season_number, state.get("base_meta") or state.get("meta") or {})
            FRESH[key] = chosen_query
            temp.GETALL[key] = files
            # Render the same detail message immediately with the new poster.
            state["message_id"] = query.message.id
            await _pro_render_detail(client, query, key, 0, push_history=False)
            return

    search = FRESH.get(key).replace("_", " ")

    if season_tag == "homepage":
        search_final = search
        query_input = search_final
    else:
        season_number = int(season_tag[1:])
        query_input = generate_season_variations(search, season_number)
        search_final = query_input[0] if query_input else search

    BUTTONS[key] = search_final
    try:
        if int(query.from_user.id) not in [query.message.reply_to_message.from_user.id, 0]:
            return await query.answer("⚠️ Not your request", show_alert=True)
    except Exception:
        pass

    chat_id = query.message.chat.id
    req = query.from_user.id
    files, n_offset, total_results = await get_search_results(chat_id, query_input, offset=0, filter=True)
    if not files:
        BUTTONS[key] = None
        return await query.answer("🚫 ɴᴏ ꜰɪʟᴇꜱ ꜰᴏᴜɴᴅ 🚫", show_alert=True)

    temp.GETALL[key] = files
    settings = await get_settings(chat_id)
    btn: list[list[InlineKeyboardButton]] = []
    if settings.get("button"):
        btn.extend(
            [
                [
                    InlineKeyboardButton(
                        _pro_file_btn(f),
                        callback_data=f"file#{f.file_id}",
                    )
                ]
                for f in files
            ]
        )
    btn.insert(
        0,
        [
            InlineKeyboardButton("Qᴜᴀʟɪᴛʏ", callback_data=f"qualities#{key}"),
            InlineKeyboardButton("✦ Language", callback_data=f"languages#{key}"),
            InlineKeyboardButton("Sᴇᴀꜱᴏɴ", callback_data=f"seasons#{key}"),
        ],
    )
    btn.insert(
        0,
        [
            InlineKeyboardButton(
                "✦ Premium", url=f"https://t.me/{temp.U_NAME}?start=premium"),
            InlineKeyboardButton("✦ Send All", callback_data=f"sendfiles#{key}"),
        ],
    )
    if n_offset != "":
        try:
            if settings['max_btn']:
                btn.append(
                    [InlineKeyboardButton("Page", callback_data="pages"), InlineKeyboardButton(
                        text=f"1/{math.ceil(int(total_results)/10)}", callback_data="pages"), InlineKeyboardButton(text="Next ›", callback_data=f"next_{req}_{key}_{n_offset}")]
                )

            else:
                btn.append(
                    [InlineKeyboardButton("Page", callback_data="pages"), InlineKeyboardButton(
                        text=f"1/{math.ceil(int(total_results)/int(MAX_B_TN))}", callback_data="pages"), InlineKeyboardButton(text="Next ›", callback_data=f"next_{req}_{key}_{n_offset}")]
                )
        except KeyError:
            await save_group_settings(query.message.chat.id, 'max_btn', True)
            btn.append(
                [InlineKeyboardButton("Page", callback_data="pages"), InlineKeyboardButton(
                    text=f"1/{math.ceil(int(total_results)/10)}", callback_data="pages"), InlineKeyboardButton(text="Next ›", callback_data=f"next_{req}_{key}_{n_offset}")]
            )
    else:
        n_offset = 0
        btn.append(
            [InlineKeyboardButton(
                "↭  ɴᴏ ᴍᴏʀᴇ ᴘᴀɢᴇꜱ ᴀᴠᴀɪʟᴀʙʟᴇ ↭", callback_data="pages")]
        )
    if not settings.get("button"):
        curr_time = datetime.now(pytz.timezone("Asia/Kolkata")).time()
        time_difference = timedelta(
            hours=curr_time.hour,
            minutes=curr_time.minute,
            seconds=curr_time.second + curr_time.microsecond / 1_000_000,
        )
        remaining_seconds = f"{time_difference.total_seconds():.2f}"
        dreamx_title = clean_search_text(search_final)
        cap = await get_cap(settings, remaining_seconds, files, query, total_results, dreamx_title, offset=1)
        try:
            await query.message.edit_text(
                text=cap,
                reply_markup=InlineKeyboardMarkup(btn),
                disable_web_page_preview=True,
            )
        except MessageNotModified:
            pass
    else:
        try:
            btn = _pro_add_back_to_titles(btn, key)
            await query.edit_message_reply_markup(InlineKeyboardMarkup(btn))
        except MessageNotModified:
            pass
    await query.answer()



@Client.on_callback_query(filters.regex(r"^reqowner#"))
async def request_owner_cb(client, query: CallbackQuery):
    try:
        _, req_key = query.data.split("#", 1)
    except ValueError:
        return await query.answer("Invalid request.", show_alert=True)

    cached = OWNER_REQ_CACHE.get(req_key)
    if not cached:
        return await query.answer("This request has expired. Please search again.", show_alert=True)

    content = cached["query"]
    reporter = str(cached["user_id"])
    mention = cached.get("mention", "User")

    if OWNER_CHAT_GROUP is None:
        return await query.answer("Request system is not configured. Contact admin.", show_alert=True)

    try:
        sent = await client.send_message(
            chat_id=OWNER_CHAT_GROUP,
            text=(
                f"<b>📝 Movie/Series Request</b>\n\n"
                f"🎬 Query : <code>{content}</code>\n"
                f"👤 Requested by : {mention}\n"
                f"🆔 User id : <code>{reporter}</code>"
            ),
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ Added, Search Again", callback_data=f"reqowner_added#{req_key}"),
                InlineKeyboardButton("💬 Other Message", callback_data=f"reqowner_custom#{req_key}"),
            ]])
        )
        # Persist so admin can reply anytime later, even after a restart
        await db.misc.update_one(
            {"_id": f"ownerreq_{req_key}"},
            {"$set": {"user_id": int(reporter), "query": content, "mention": mention}},
            upsert=True
        )
    except Exception as e:
        logger.exception("Failed to forward owner request: %s", e)
        return await query.answer("Failed to send request. Try again later.", show_alert=True)

    OWNER_REQ_CACHE.pop(req_key, None)
    try:
        await query.message.edit_text(script.REQUEST_SENT_TXT)
    except Exception:
        pass
    await query.answer("Request sent to the owner!", show_alert=True)


@Client.on_callback_query(filters.regex(r"^reqowner_added#"))
async def request_owner_added_cb(client, query: CallbackQuery):
    if query.from_user.id not in ADMINS:
        return await query.answer("Only admins can respond to requests.", show_alert=True)
    req_key = query.data.split("#", 1)[1]
    doc = await db.misc.find_one({"_id": f"ownerreq_{req_key}"})
    if not doc:
        return await query.answer("This request record was not found (maybe already handled).", show_alert=True)
    try:
        await client.send_message(
            chat_id=doc["user_id"],
            text=f"✅ <b>Your requested movie/series has been added!</b>\n\n🎬 <code>{doc.get('query', '')}</code>\n\nPlease search for it again 🙂"
        )
    except Exception as e:
        return await query.answer(f"Could not message the user: {e}", show_alert=True)
    try:
        await query.message.edit_reply_markup(InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Marked Added — user notified", callback_data="noop")
        ]]))
    except Exception:
        pass
    await db.misc.delete_one({"_id": f"ownerreq_{req_key}"})
    await query.answer("✅ User notified that it's added!", show_alert=True)


@Client.on_callback_query(filters.regex(r"^reqowner_custom#"))
async def request_owner_custom_cb(client, query: CallbackQuery):
    if query.from_user.id not in ADMINS:
        return await query.answer("Only admins can respond to requests.", show_alert=True)
    req_key = query.data.split("#", 1)[1]
    doc = await db.misc.find_one({"_id": f"ownerreq_{req_key}"})
    if not doc:
        return await query.answer("This request record was not found (maybe already handled).", show_alert=True)
    ADMIN_AWAITING_REPLY[query.from_user.id] = req_key
    await query.answer("Now type your reply message in this chat — it will be sent to the user.", show_alert=True)


@Client.on_message(filters.text & filters.create(lambda _, __, m: m.from_user and m.from_user.id in ADMIN_AWAITING_REPLY), group=-1)
async def owner_custom_reply_handler(client, message):
    admin_id = message.from_user.id
    req_key = ADMIN_AWAITING_REPLY.pop(admin_id, None)
    if not req_key:
        return
    doc = await db.misc.find_one({"_id": f"ownerreq_{req_key}"})
    if not doc:
        return await message.reply_text("⚠️ This request record was not found (maybe expired).")
    try:
        await client.send_message(
            chat_id=doc["user_id"],
            text=f"📩 <b>Message from the owner regarding your request</b> (<code>{doc.get('query', '')}</code>):\n\n{message.text}"
        )
        await message.reply_text("✅ Your message has been sent to the user.")
    except Exception as e:
        await message.reply_text(f"❌ Could not message the user: {e}")
    await db.misc.delete_one({"_id": f"ownerreq_{req_key}"})


def _pro_add_back_to_titles(btn, key):
    if key in PRO_DETAIL:
        btn.append([InlineKeyboardButton("🎬 Back to Movies", callback_data=f"mback#{PRO_DETAIL[key]['search_key']}")])
    return btn


@Client.on_callback_query()
async def cb_handler(client: Client, query: CallbackQuery):
    DreamxData = query.data
    if DreamxData and str(DreamxData).startswith("ui_"):
        from plugins.ui_pro import dispatch_ui
        await dispatch_ui(query)
        return
    try:
        link = await client.create_chat_invite_link(int(REQST_CHANNEL))
    except:
        pass
    if query.data == "close_data":
        try:
            user = query.message.reply_to_message.from_user.id
        except:
            user = query.from_user.id
        if int(user) != 0 and query.from_user.id != int(user):
            return await query.answer(script.NT_ALRT_TXT, show_alert=True)
        await query.answer("ᴛʜᴀɴᴋs ꜰᴏʀ ᴄʟᴏsᴇ 🙈")
        await query.message.delete()
        try:
            await query.message.reply_to_message.delete()
        except:
            pass

    elif query.data == "pages":
        await query.answer("ᴛʜɪs ɪs ᴘᴀɢᴇs ʙᴜᴛᴛᴏɴ 😅")

    elif query.data == "hiding":
        await query.answer("ʙᴇᴄᴀᴜsᴇ ᴏғ ʟᴀɢᴛᴇ ғɪʟᴇs ɪɴ ᴅᴀᴛᴀʙᴀsᴇ,🙏\nɪᴛ ᴛᴀᴋᴇꜱ ʟɪᴛᴛʟᴇ ʙɪᴛ ᴛɪᴍᴇ",show_alert=True)

    elif query.data == "delallcancel":
        userid = query.from_user.id
        chat_type = query.message.chat.type
        if chat_type == enums.ChatType.PRIVATE:
            await query.message.reply_to_message.delete()
            await query.message.delete()
        elif chat_type in [enums.ChatType.GROUP, enums.ChatType.SUPERGROUP]:
            grp_id = query.message.chat.id
            st = await client.get_chat_member(grp_id, userid)
            if (st.status == enums.ChatMemberStatus.OWNER) or (str(userid) in ADMINS):
                await query.message.delete()
                try:
                    await query.message.reply_to_message.delete()
                except:
                    pass
            else:
                await query.answer("Tʜᴀᴛ's ɴᴏᴛ ғᴏʀ ʏᴏᴜ!!", show_alert=True)

    if query.data.startswith("file"):
        ident, file_id = query.data.split("#")
        user = query.message.reply_to_message.from_user.id if query.message.reply_to_message else query.from_user.id
        if int(user) != 0 and query.from_user.id != int(user):
            return await query.answer(script.ALRT_TXT.format(query.from_user.first_name), show_alert=True)
        await query.answer(url=f"https://t.me/{temp.U_NAME}?start=file_{query.message.chat.id}_{file_id}")

    elif query.data.startswith("sendfiles"):
        clicked = query.from_user.id
        ident, key = query.data.split("#")
        settings = await get_settings(query.message.chat.id)
        try:
            await query.answer(url=f"https://telegram.me/{temp.U_NAME}?start=allfiles_{query.message.chat.id}_{key}")
            return
        except UserIsBlocked:
            await query.answer('Uɴʙʟᴏᴄᴋ ᴛʜᴇ ʙᴏᴛ ᴍᴀʜɴ !', show_alert=True)
        except PeerIdInvalid:
            await query.answer(url=f"https://telegram.me/{temp.U_NAME}?start=sendfiles3_{key}")
        except Exception as e:
            logger.exception(e)
            await query.answer(url=f"https://telegram.me/{temp.U_NAME}?start=sendfiles4_{key}")

    elif query.data.startswith("del"):
        ident, file_id = query.data.split("#")
        files_ = await get_file_details(file_id)
        if not files_:
            return await query.answer('Nᴏ sᴜᴄʜ ғɪʟᴇ ᴇxɪsᴛ.')
        files = files_[0]
        title = files.file_name
        size = get_size(files.file_size)
        f_caption = files.caption
        settings = await get_settings(query.message.chat.id)
        if CUSTOM_FILE_CAPTION:
            try:
                f_caption = format_file_caption(CUSTOM_FILE_CAPTION, title, size, f_caption)
            except Exception as e:
                logger.exception(e)
            f_caption = f_caption
        if f_caption is None:
            f_caption = f"{files.file_name}"
        await query.answer(url=f"href='https://telegram.me/{temp.U_NAME}?start=file_{query.message.chat.id}_{file.file_id}")

    elif query.data.startswith("autofilter_delete"):
        await Media.collection.drop()
        if MULTIPLE_DB:    
            await Media2.collection.drop()
        await query.answer("Eᴠᴇʀʏᴛʜɪɴɢ's Gᴏɴᴇ")
        await query.message.edit('ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ ᴀʟʟ ɪɴᴅᴇxᴇᴅ ꜰɪʟᴇꜱ ✅')

    elif query.data.startswith("checksub"):
        try:
            ident, kk, file_id = query.data.split("#")
            btn = []
            chat = file_id.split("_")[0]
            settings = await get_settings(chat)
            fsub_channels = list(dict.fromkeys((settings.get('fsub', []) if settings else [])+ AUTH_CHANNELS)) 
            btn += await is_subscribed(client, query.from_user.id, fsub_channels)
            btn += await is_req_subscribed(client, query.from_user.id, AUTH_REQ_CHANNELS)
            if btn:
                btn.append([InlineKeyboardButton("♻️ ᴛʀʏ ᴀɢᴀɪɴ ♻️", callback_data=f"checksub#{kk}#{file_id}")])
                try:
                    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(btn))
                except MessageNotModified:
                    pass
                await query.answer(
                    f"👋 Hello {query.from_user.first_name},\n\n"
                    "🛑 Yᴏᴜ ʜᴀᴠᴇ ɴᴏᴛ ᴊᴏɪɴᴇᴅ ᴀʟʟ ʀᴇǫᴜɪʀᴇᴅ ᴜᴘᴅᴀᴛᴇ Cʜᴀɴɴᴇʟs.\n"
                    "👉 Pʟᴇᴀsᴇ ᴊᴏɪɴ ᴇᴀᴄʜ ᴏɴᴇ ᴀɴᴅ ᴛʀʏ ᴀɢᴀɪɴ.\n",
                    show_alert=True
                )
                return
            await query.answer(url=f"https://t.me/{temp.U_NAME}?start={kk}_{file_id}")
            await query.message.delete()
        except Exception as e:
            await log_error(client, f"❌ Error in checksub callback:\n\n{repr(e)}")
            logger.error(f"❌ Error in checksub callback:\n\n{repr(e)}")


    elif query.data.startswith("killfilesdq"):
        ident, keyword = query.data.split("#")
        await query.message.edit_text(f"<b>Fetching Files for your query {keyword} on DB... Please wait...</b>")
        files, total = await get_bad_files(keyword)
        await query.message.edit_text("<b>ꜰɪʟᴇ ᴅᴇʟᴇᴛɪᴏɴ ᴘʀᴏᴄᴇꜱꜱ ᴡɪʟʟ ꜱᴛᴀʀᴛ ɪɴ 5 ꜱᴇᴄᴏɴᴅꜱ !</b>")
        await asyncio.sleep(5)
        deleted = 0
        async with lock:
            try:
                for file in files:
                    file_ids = file.file_id
                    file_name = file.file_name
                    result = await Media.collection.delete_one({
                        '_id': file_ids,
                    })
                    if not result.deleted_count and MULTIPLE_DB:
                        result = await Media2.collection.delete_one({
                            '_id': file_ids,
                        })
                    if result.deleted_count:
                        logger.info(
                            f'ꜰɪʟᴇ ꜰᴏᴜɴᴅ ꜰᴏʀ ʏᴏᴜʀ ǫᴜᴇʀʏ {keyword}! ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ {file_name} ꜰʀᴏᴍ ᴅᴀᴛᴀʙᴀꜱᴇ.')
                    deleted += 1
                    if deleted % 20 == 0:
                        await query.message.edit_text(f"<b>ᴘʀᴏᴄᴇꜱꜱ ꜱᴛᴀʀᴛᴇᴅ ꜰᴏʀ ᴅᴇʟᴇᴛɪɴɢ ꜰɪʟᴇꜱ ꜰʀᴏᴍ ᴅʙ. ꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ {str(deleted)} ꜰɪʟᴇꜱ ꜰʀᴏᴍ ᴅʙ ꜰᴏʀ ʏᴏᴜʀ ǫᴜᴇʀʏ {keyword} !\n\nᴘʟᴇᴀꜱᴇ ᴡᴀɪᴛ...</b>")
            except Exception as e:
                print(f"Error In killfiledq -{e}")
                await query.message.edit_text(f'Error: {e}')
            else:
                await query.message.edit_text(f"<b>ᴘʀᴏᴄᴇꜱꜱ ᴄᴏᴍᴘʟᴇᴛᴇᴅ ꜰᴏʀ ꜰɪʟᴇ ᴅᴇʟᴇᴛᴀᴛɪᴏɴ !\n\nꜱᴜᴄᴄᴇꜱꜱꜰᴜʟʟʏ ᴅᴇʟᴇᴛᴇᴅ {str(deleted)} ꜰɪʟᴇꜱ ꜰʀᴏᴍ ᴅʙ ꜰᴏʀ ʏᴏᴜʀ ǫᴜᴇʀʏ {keyword}.</b>")

    elif query.data.startswith("opnsetgrp"):
        ident, grp_id = query.data.split("#")
        userid = query.from_user.id if query.from_user else None
        st = await client.get_chat_member(grp_id, userid)
        if (
                st.status != enums.ChatMemberStatus.ADMINISTRATOR
                and st.status != enums.ChatMemberStatus.OWNER
                and str(userid) not in ADMINS
        ):
            await query.answer("ʏᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ ʀɪɢʜᴛꜱ ᴛᴏ ᴅᴏ ᴛʜɪꜱ !", show_alert=True)
            return
        title = query.message.chat.title
        settings = await get_settings(grp_id)
        if settings is not None:
            btn = await group_setting_buttons(int(grp_id))
            reply_markup = InlineKeyboardMarkup(btn)
            await query.message.edit_text(
                text=f"<b>ᴄʜᴀɴɢᴇ ʏᴏᴜʀ ꜱᴇᴛᴛɪɴɢꜱ ꜰᴏʀ {title} ᴀꜱ ʏᴏᴜ ᴡɪꜱʜ ⚙</b>",
                disable_web_page_preview=True,
                parse_mode=enums.ParseMode.HTML
            )
            await query.message.edit_reply_markup(reply_markup)

    elif query.data.startswith("opnsetpm"):
        ident, grp_id = query.data.split("#")
        userid = query.from_user.id if query.from_user else None
        st = await client.get_chat_member(grp_id, userid)
        if (
                st.status != enums.ChatMemberStatus.ADMINISTRATOR
                and st.status != enums.ChatMemberStatus.OWNER
                and str(userid) not in ADMINS
        ):
            await query.answer("Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ sᴜғғɪᴄɪᴀɴᴛ ʀɪɢʜᴛs ᴛᴏ ᴅᴏ ᴛʜɪs !", show_alert=True)
            return
        title = query.message.chat.title
        settings = await get_settings(grp_id)
        btn2 = [[
            InlineKeyboardButton(
                "ᴄʜᴇᴄᴋ ᴍʏ ᴅᴍ 🗳️", url=f"telegram.me/{temp.U_NAME}")
        ]]
        reply_markup = InlineKeyboardMarkup(btn2)
        await query.message.edit_text(f"<b>ʏᴏᴜʀ sᴇᴛᴛɪɴɢs ᴍᴇɴᴜ ғᴏʀ {title} ʜᴀs ʙᴇᴇɴ sᴇɴᴛ ᴛᴏ ʏᴏᴜ ʙʏ ᴅᴍ.</b>")
        await query.message.edit_reply_markup(reply_markup)
        if settings is not None:
            btn = await group_setting_buttons(int(grp_id))
            reply_markup = InlineKeyboardMarkup(btn)
            await client.send_message(
                chat_id=userid,
                text=f"<b>ᴄʜᴀɴɢᴇ ʏᴏᴜʀ ꜱᴇᴛᴛɪɴɢꜱ ꜰᴏʀ {title} ᴀꜱ ʏᴏᴜ ᴡɪꜱʜ ⚙</b>",
                reply_markup=reply_markup,
                disable_web_page_preview=True,
                parse_mode=enums.ParseMode.HTML,
                reply_to_message_id=query.message.id
            )

    elif query.data.startswith("show_option"):
        ident, from_user = query.data.split("#")
        btn = [[
            InlineKeyboardButton("⚠️ ᴜɴᴀᴠᴀɪʟᴀʙʟᴇ ⚠️",
                                 callback_data=f"unavailable#{from_user}"),
            InlineKeyboardButton(
                "🟢 ᴜᴘʟᴏᴀᴅᴇᴅ 🟢", callback_data=f"uploaded#{from_user}")
        ], [
            InlineKeyboardButton("♻️ ᴀʟʀᴇᴀᴅʏ ᴀᴠᴀɪʟᴀʙʟᴇ ♻️",
                                 callback_data=f"already_available#{from_user}")
        ], [
            InlineKeyboardButton("📌 Not Released 📌",
                                 callback_data=f"Not_Released#{from_user}"),
            InlineKeyboardButton("♨️Type Correct Spelling♨️",
                                 callback_data=f"Type_Correct_Spelling#{from_user}")
        ], [
            InlineKeyboardButton("⚜️ Not Available In The Hindi ⚜️",
                                 callback_data=f"Not_Available_In_The_Hindi#{from_user}")
        ]]
        btn2 = [[
            InlineKeyboardButton("ᴠɪᴇᴡ ꜱᴛᴀᴛᴜꜱ", url=f"{query.message.link}")
        ]]
        if query.from_user.id in ADMINS:
            user = await client.get_users(from_user)
            reply_markup = InlineKeyboardMarkup(btn)
            await query.message.edit_reply_markup(reply_markup)
            await query.answer("Hᴇʀᴇ ᴀʀᴇ ᴛʜᴇ ᴏᴘᴛɪᴏɴs !")
        else:
            await query.answer("Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ sᴜғғɪᴄɪᴀɴᴛ ʀɪɢʜᴛs ᴛᴏ ᴅᴏ ᴛʜɪs !", show_alert=True)

    elif query.data.startswith("unavailable"):
        ident, from_user = query.data.split("#")
        btn = [
            [InlineKeyboardButton("⚠️ ᴜɴᴀᴠᴀɪʟᴀʙʟᴇ ⚠️",
                                  callback_data=f"unalert#{from_user}")]
        ]
        btn2 = [
            [InlineKeyboardButton('ᴊᴏɪɴ ᴄʜᴀɴɴᴇʟ', url=link.invite_link),
             InlineKeyboardButton("ᴠɪᴇᴡ ꜱᴛᴀᴛᴜꜱ", url=f"{query.message.link}")]
        ]
        if query.from_user.id in ADMINS:
            user = await client.get_users(from_user)
            reply_markup = InlineKeyboardMarkup(btn)
            content = query.message.text
            await query.message.edit_text(f"<b><strike>{content}</strike></b>")
            await query.message.edit_reply_markup(reply_markup)
            await query.answer("Sᴇᴛ ᴛᴏ Uɴᴀᴠᴀɪʟᴀʙʟᴇ !")
            content = extract_request_content(query.message.text)
            try:
                await client.send_message(
                    chat_id=int(from_user),
                    text=f"<b>Hᴇʏ {user.mention},</b>\n\n<u>{content}</u> Hᴀs Bᴇᴇɴ Mᴀʀᴋᴇᴅ Aᴅ ᴜɴᴀᴠᴀɪʟᴀʙʟᴇ...💔\n\n#Uɴᴀᴠᴀɪʟᴀʙʟᴇ ⚠️",
                    reply_markup=InlineKeyboardMarkup(btn2)
                )
            except UserIsBlocked:
                await client.send_message(
                    chat_id=int(SUPPORT_CHAT_ID),
                    text=f"<b>Hᴇʏ {user.mention},</b>\n\n<u>{content}</u> Hᴀs Bᴇᴇɴ Mᴀʀᴋᴇᴅ Aᴅ ᴜɴᴀᴠᴀɪʟᴀʙʟᴇ...💔\n\n#Uɴᴀᴠᴀɪʟᴀʙʟᴇ ⚠️\n\n<small>Bʟᴏᴄᴋᴇᴅ? Uɴʙʟᴏᴄᴋ ᴛʜᴇ ʙᴏᴛ ᴛᴏ ʀᴇᴄᴇɪᴠᴇ ᴍᴇꜱꜱᴀɢᴇꜱ.</small></b>",
                    reply_markup=InlineKeyboardMarkup(btn2)
                )

    elif query.data.startswith("Not_Released"):
        ident, from_user = query.data.split("#")
        btn = [[InlineKeyboardButton(
            "📌 Not Released 📌", callback_data=f"nralert#{from_user}")]]
        btn2 = [[
            InlineKeyboardButton('ᴊᴏɪɴ ᴄʜᴀɴɴᴇʟ', url=link.invite_link),
            InlineKeyboardButton("ᴠɪᴇᴡ ꜱᴛᴀᴛᴜꜱ", url=f"{query.message.link}")
        ]]
        if query.from_user.id in ADMINS:
            user = await client.get_users(from_user)
            reply_markup = InlineKeyboardMarkup(btn)
            content = query.message.text
            await query.message.edit_text(f"<b><strike>{content}</strike></b>")
            await query.message.edit_reply_markup(reply_markup)
            await query.answer("Sᴇᴛ ᴛᴏ Nᴏᴛ Rᴇʟᴇᴀꜱᴇᴅ !")
            content = extract_request_content(query.message.text)
            try:
                await client.send_message(
                    chat_id=int(from_user),
                    text=(
                        f"<b>Hᴇʏ {user.mention}\n\n"
                        f"<code>{content}</code>, ʏᴏᴜʀ ʀᴇǫᴜᴇꜱᴛ ʜᴀꜱ ɴᴏᴛ ʙᴇᴇɴ ʀᴇʟᴇᴀꜱᴇᴅ ʏᴇᴛ\n\n"
                        f"#CᴏᴍɪɴɢSᴏᴏɴ...🕊️✌️</b>"
                    ),
                    reply_markup=InlineKeyboardMarkup(btn2)
                )
            except UserIsBlocked:
                await client.send_message(
                    chat_id=int(SUPPORT_CHAT_ID),
                    text=(
                        f"<u>Hᴇʏ {user.mention}</u>\n\n"
                        f"<b><code>{content}</code>, ʏᴏᴜʀ ʀᴇǫᴜᴇꜱᴛ ʜᴀꜱ ɴᴏᴛ ʙᴇᴇɴ ʀᴇʟᴇᴀꜱᴇᴅ ʏᴇᴛ\n\n"
                        f"#CᴏᴍɪɴɢSᴏᴏɴ...🕊️✌️\n\n"
                        f"<small>Bʟᴏᴄᴋᴇᴅ? Uɴʙʟᴏᴄᴋ ᴛʜᴇ ʙᴏᴛ ᴛᴏ ʀᴇᴄᴇɪᴠᴇ ᴍᴇꜱꜱᴀɢᴇꜱ.</small></b>"
                    ),
                    reply_markup=InlineKeyboardMarkup(btn2)
                )
        else:
            await query.answer("Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ sᴜғғɪᴄɪᴀɴᴛ ʀɪɢʜᴛs ᴛᴏ ᴅᴏ ᴛʜɪs !", show_alert=True)

    elif query.data.startswith("Type_Correct_Spelling"):
        ident, from_user = query.data.split("#")
        btn = [[
            InlineKeyboardButton("♨️ Type Correct Spelling ♨️",
                                 callback_data=f"wsalert#{from_user}")
        ]]
        btn2 = [[
            InlineKeyboardButton('ᴊᴏɪɴ ᴄʜᴀɴɴᴇʟ', url=link.invite_link),
            InlineKeyboardButton("ᴠɪᴇᴡ ꜱᴛᴀᴛᴜꜱ", url=f"{query.message.link}")
        ]]
        if query.from_user.id in ADMINS:
            user = await client.get_users(from_user)
            reply_markup = InlineKeyboardMarkup(btn)
            content = query.message.text
            await query.message.edit_text(f"<b><strike>{content}</strike></b>")
            await query.message.edit_reply_markup(reply_markup)
            await query.answer("Sᴇᴛ ᴛᴏ Cᴏʀʀᴇᴄᴛ Sᴘᴇʟʟɪɴɢ !")
            content = extract_request_content(query.message.text)
            try:
                await client.send_message(
                    chat_id=int(from_user),
                    text=(
                        f"<b>Hᴇʏ {user.mention}\n\n"
                        f"Wᴇ Dᴇᴄʟɪɴᴇᴅ Yᴏᴜʀ Rᴇǫᴜᴇsᴛ <code>{content}</code>, Bᴇᴄᴀᴜsᴇ Yᴏᴜʀ Sᴘᴇʟʟɪɴɢ Wᴀs Wʀᴏɴɢ 😢\n\n"
                        f"#Wʀᴏɴɢ_Sᴘᴇʟʟɪɴɢ 😑</b>"
                    ),
                    reply_markup=InlineKeyboardMarkup(btn2)
                )
            except UserIsBlocked:
                await client.send_message(
                    chat_id=int(SUPPORT_CHAT_ID),
                    text=(
                        f"<u>Hᴇʏ {user.mention}</u>\n\n"
                        f"<b><code>{content}</code>, Bᴇᴄᴀᴜsᴇ Yᴏᴜʀ Sᴘᴇʟʟɪɴɢ Wᴀs Wʀᴏɴɢ 😢\n\n"
                        f"#Wʀᴏɴɢ_Sᴘᴇʟʟɪɴɢ 😑\n\n"
                        f"<small>Bʟᴏᴄᴋᴇᴅ? Uɴʙʟᴏᴄᴋ ᴛʜᴇ ʙᴏᴛ ᴛᴏ ʀᴇᴄᴇɪᴠᴇ ᴍᴇꜱꜱᴀɢᴇꜱ.</small></b>"
                    ),
                    reply_markup=InlineKeyboardMarkup(btn2)
                )
        else:
            await query.answer("Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ sᴜғғɪᴄɪᴀɴᴛ ʀɪɢʜᴛs ᴛᴏ ᴅᴏ ᴛʜɪs !", show_alert=True)

    elif query.data.startswith("Not_Available_In_The_Hindi"):
        ident, from_user = query.data.split("#")
        btn = [[
            InlineKeyboardButton(
                "⚜️ Not Available In The Hindi ⚜️", callback_data=f"hnalert#{from_user}")
        ]]
        btn2 = [[
            InlineKeyboardButton('ᴊᴏɪɴ ᴄʜᴀɴɴᴇʟ', url=link.invite_link),
            InlineKeyboardButton("ᴠɪᴇᴡ ꜱᴛᴀᴛᴜꜱ", url=f"{query.message.link}")
        ]]
        if query.from_user.id in ADMINS:
            user = await client.get_users(from_user)
            reply_markup = InlineKeyboardMarkup(btn)
            content = query.message.text
            await query.message.edit_text(f"<b><strike>{content}</strike></b>")
            await query.message.edit_reply_markup(reply_markup)
            await query.answer("Sᴇᴛ ᴛᴏ Nᴏᴛ Aᴠᴀɪʟᴀʙʟᴇ Iɴ Hɪɴᴅɪ !")
            content = extract_request_content(query.message.text)
            try:
                await client.send_message(
                    chat_id=int(from_user),
                    text=(
                        f"<b>Hᴇʏ {user.mention}\n\n"
                        f"Yᴏᴜʀ Rᴇǫᴜᴇsᴛ <code>{content}</code> ɪs Nᴏᴛ Aᴠᴀɪʟᴀʙʟᴇ ɪɴ Hɪɴᴅɪ ʀɪɢʜᴛ ɴᴏᴡ. Sᴏ ᴏᴜʀ ᴍᴏᴅᴇʀᴀᴛᴏʀs ᴄᴀɴ'ᴛ ᴜᴘʟᴏᴀᴅ ɪᴛ\n\n"
                        f"#Hɪɴᴅɪ_ɴᴏᴛ_ᴀᴠᴀɪʟᴀʙʟᴇ ❌</b>"
                    ),
                    reply_markup=InlineKeyboardMarkup(btn2)
                )
            except UserIsBlocked:
                await client.send_message(
                    chat_id=int(SUPPORT_CHAT_ID),
                    text=(
                        f"<u>Hᴇʏ {user.mention}</u>\n\n"
                        f"<b><code>{content}</code> ɪs Nᴏᴛ Aᴠᴀɪʟᴀʙʟᴇ ɪɴ Hɪɴᴅɪ ʀɪɢʜᴛ ɴᴏᴡ. Sᴏ ᴏᴜʀ ᴍᴏᴅᴇʀᴀᴛᴏʀs ᴄᴀɴ'ᴛ ᴜᴘʟᴏᴀᴅ ɪᴛ\n\n"
                        f"#Hɪɴᴅɪ_ɴᴏᴛ_ᴀᴠᴀɪʟᴀʙʟᴇ ❌\n\n"
                        f"<small>Bʟᴏᴄᴋᴇᴅ? Uɴʙʟᴏᴄᴋ ᴛʜᴇ ʙᴏᴛ ᴛᴏ ʀᴇᴄᴇɪᴠᴇ ᴍᴇꜱꜱᴀɢᴇꜱ.</small></b>"
                    ),
                    reply_markup=InlineKeyboardMarkup(btn2)
                )
        else:
            await query.answer("Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ sᴜғғɪᴄɪᴀɴᴛ ʀɪɢʜᴛs ᴛᴏ ᴅᴏ ᴛʜɪs !", show_alert=True)

    elif query.data.startswith("uploaded"):
        ident, from_user = query.data.split("#")
        btn = [[
            InlineKeyboardButton(
                "🟢 ᴜᴘʟᴏᴀᴅᴇᴅ 🟢", callback_data=f"upalert#{from_user}")
        ]]
        movie_name = extract_request_content(query.message.text)
        deep_payload = re.sub(r'[^A-Za-z0-9]+', '-', movie_name).strip('-')
        getfile_link = f"https://t.me/{temp.U_NAME}?start=getfile-{deep_payload}"
        btn2 = [[
            InlineKeyboardButton("📥 ɢᴇᴛ ꜰɪʟᴇ ɪɴ ᴘᴍ 📥", url=getfile_link)
        ], [
            InlineKeyboardButton('ᴊᴏɪɴ ᴄʜᴀɴɴᴇʟ', url=link.invite_link),
            InlineKeyboardButton("ᴠɪᴇᴡ ꜱᴛᴀᴛᴜꜱ", url=f"{query.message.link}")
        ], [
            InlineKeyboardButton("🔍 ꜱᴇᴀʀᴄʜ ʜᴇʀᴇ 🔎", url=GRP_LNK)
        ]]
        if query.from_user.id in ADMINS:
            user = await client.get_users(from_user)
            reply_markup = InlineKeyboardMarkup(btn)
            content = query.message.text
            await query.message.edit_text(f"<b><strike>{content}</strike></b>")
            await query.message.edit_reply_markup(reply_markup)
            await query.answer("Sᴇᴛ ᴛᴏ Uᴘʟᴏᴀᴅᴇᴅ !")
            content = extract_request_content(query.message.text)
            try:
                await client.send_message(
                    chat_id=int(from_user),
                    text=(
                        f"<b>Hᴇʏ {user.mention},\n\n"
                        f"<u>{content}</u> Yᴏᴜʀ ʀᴇǫᴜᴇꜱᴛ ʜᴀꜱ ʙᴇᴇɴ ᴜᴘʟᴏᴀᴅᴇᴅ ʙʏ ᴏᴜʀ ᴍᴏᴅᴇʀᴀᴛᴏʀs.\n"
                        f"Kɪɴᴅʟʏ sᴇᴀʀᴄʜ ɪɴ ᴏᴜʀ Gʀᴏᴜᴘ.</b>\n\n"
                        f"#Uᴘʟᴏᴀᴅᴇᴅ✅"
                    ),
                    reply_markup=InlineKeyboardMarkup(btn2)
                )
            except UserIsBlocked:
                await client.send_message(
                    chat_id=int(SUPPORT_CHAT_ID),
                    text=(
                        f"<u>{content}</u>\n\n"
                        f"<b>Hᴇʏ {user.mention}, Yᴏᴜʀ ʀᴇǫᴜᴇꜱᴛ ʜᴀꜱ ʙᴇᴇɴ ᴜᴘʟᴏᴀᴅᴇᴅ ʙʏ ᴏᴜʀ ᴍᴏᴅᴇʀᴀᴛᴏʀs."
                        f"Kɪɴᴅʟʏ sᴇᴀʀᴄʜ ɪɴ ᴏᴜʀ Gʀᴏᴜᴘ.</b>\n\n"
                        f"#Uᴘʟᴏᴀᴅᴇᴅ✅\n\n"
                        f"<small>Bʟᴏᴄᴋᴇᴅ? Uɴʙʟᴏᴄᴋ ᴛʜᴇ ʙᴏᴛ ᴛᴏ ʀᴇᴄᴇɪᴠᴇ ᴍᴇꜱꜱᴀɢᴇꜱ.</small>"
                    ),
                    reply_markup=InlineKeyboardMarkup(btn2)
                )
        else:
            await query.answer("Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ sᴜғғɪᴄɪᴀɴᴛ ʀɪɢᴛs ᴛᴏ ᴅᴏ ᴛʜɪs !", show_alert=True)

    elif query.data.startswith("already_available"):
        ident, from_user = query.data.split("#")
        btn = [[
            InlineKeyboardButton("♻️ ᴀʟʀᴇᴀᴅʏ ᴀᴠᴀɪʟᴀʙʟᴇ ♻️",
                                 callback_data=f"alalert#{from_user}")
        ]]
        movie_name = extract_request_content(query.message.text)
        deep_payload = re.sub(r'[^A-Za-z0-9]+', '-', movie_name).strip('-')
        getfile_link = f"https://t.me/{temp.U_NAME}?start=getfile-{deep_payload}"
        btn2 = [[
            InlineKeyboardButton("📥 ɢᴇᴛ ꜰɪʟᴇ ɪɴ ᴘᴍ 📥", url=getfile_link)
        ], [
            InlineKeyboardButton('ᴊᴏɪɴ ᴄʜᴀɴɴᴇʟ', url=link.invite_link),
            InlineKeyboardButton("ᴠɪᴇᴡ ꜱᴛᴀᴛᴜꜱ", url=f"{query.message.link}")
        ], [
            InlineKeyboardButton("🔍 ꜱᴇᴀʀᴄʜ ʜᴇʀᴇ 🔎", url=GRP_LNK)
        ]]
        if query.from_user.id in ADMINS:
            user = await client.get_users(from_user)
            reply_markup = InlineKeyboardMarkup(btn)
            content = query.message.text
            await query.message.edit_text(f"<b><strike>{content}</strike></b>")
            await query.message.edit_reply_markup(reply_markup)
            await query.answer("Sᴇᴛ ᴛᴏ Aʟʀᴇᴀᴅʏ Aᴠᴀɪʟᴀʙʟᴇ !")
            content = extract_request_content(query.message.text)
            try:
                await client.send_message(
                    chat_id=int(from_user),
                    text=(
                        f"<b>Hᴇʏ {user.mention},\n\n"
                        f"<u>{content}</u> Yᴏᴜʀ ʀᴇǫᴜᴇꜱᴛ ɪꜱ ᴀʟʀᴇᴀᴅʏ ᴀᴠᴀɪʟᴀʙʟᴇ ɪɴ ᴏᴜʀ ʙᴏᴛ'ꜱ ᴅᴀᴛᴀʙᴀꜱᴇ.\n"
                        f"Kɪɴᴅʟʏ sᴇᴀʀᴄʜ ɪɴ ᴏᴜʀ Gʀᴏᴜᴘ.</b>\n\n"
                        f"#Aᴠᴀɪʟᴀʙʟᴇ 💗"
                    ),
                    reply_markup=InlineKeyboardMarkup(btn2)
                )
            except UserIsBlocked:
                await client.send_message(
                    chat_id=int(SUPPORT_CHAT_ID),
                    text=(
                        f"<b>Hᴇʏ {user.mention},\n\n"
                        f"<u>{content}</u> Yᴏᴜʀ ʀᴇǫᴜᴇꜱᴛ ɪꜱ ᴀʟʀᴇᴀᴅʏ ᴀᴠᴀɪʟᴀʙʟᴇ ɪɴ ᴏᴜʀ ʙᴏᴛ'ꜱ ᴅᴀᴛᴀʙᴀꜱᴇ.\n"
                        f"Kɪɴᴅʟʏ sᴇᴀʀᴄʜ ɪɴ ᴏᴜʀ Gʀᴏᴜᴘ.</b>\n\n"
                        f"#Aᴠᴀɪʟᴀʙʟᴇ 💗\n"
                        f"<small>Bʟᴏᴄᴋᴇᴅ? Uɴʙʟᴏᴄᴋ ᴛʜᴇ ʙᴏᴛ ᴛᴏ ʀᴇᴄᴇɪᴠᴇ ᴍᴇꜱꜱᴀɢᴇꜱ.</small></i>"
                    ),
                    reply_markup=InlineKeyboardMarkup(btn2)
                )
        else:
            await query.answer("Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ sᴜғғɪᴄɪᴀɴᴛ ʀɪɢᴛs ᴛᴏ ᴅᴏ ᴛʜɪs !", show_alert=True)

    elif query.data.startswith("alalert"):
        ident, from_user = query.data.split("#")
        if int(query.from_user.id) == int(from_user):
            user = await client.get_users(from_user)
            await query.answer(
                f"Hᴇʏ {user.first_name}, Yᴏᴜʀ ʀᴇǫᴜᴇꜱᴛ ɪꜱ Aʟʀᴇᴀᴅʏ Aᴠᴀɪʟᴀʙʟᴇ ✅",
                show_alert=True
            )
        else:
            await query.answer("Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ sᴜғғɪᴄɪᴇɴᴛ ʀɪɢʜᴛs ᴛᴏ ᴅᴏ ᴛʜɪs ❌", show_alert=True)

    elif query.data.startswith("upalert"):
        ident, from_user = query.data.split("#")
        if int(query.from_user.id) == int(from_user):
            user = await client.get_users(from_user)
            await query.answer(
                f"Hᴇʏ {user.first_name}, Yᴏᴜʀ ʀᴇǫᴜᴇꜱᴛ ɪꜱ Uᴘʟᴏᴀᴅᴇᴅ 🔼",
                show_alert=True
            )
        else:
            await query.answer("Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ sᴜғғɪᴄɪᴇɴᴛ ʀɪɢʜᴛs ᴛᴏ ᴅᴏ ᴛʜɪs ❌", show_alert=True)

    elif query.data.startswith("unalert"):
        ident, from_user = query.data.split("#")
        if int(query.from_user.id) == int(from_user):
            user = await client.get_users(from_user)
            await query.answer(
                f"Hᴇʏ {user.first_name}, Yᴏᴜʀ ʀᴇǫᴜᴇꜱᴛ ɪꜱ Uɴᴀᴠᴀɪʟᴀʙʟᴇ ⚠️",
                show_alert=True
            )
        else:
            await query.answer("Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ sᴜғғɪᴄɪᴇɴᴛ ʀɪɢʜᴛs ᴛᴏ ᴅᴏ ᴛʜɪs ❌", show_alert=True)

    elif query.data.startswith("hnalert"):
        ident, from_user = query.data.split("#")  # Hindi Not Available
        if int(query.from_user.id) == int(from_user):
            user = await client.get_users(from_user)
            await query.answer(
                f"Hᴇʏ {user.first_name}, Tʜɪꜱ ɪꜱ Nᴏᴛ Aᴠᴀɪʟᴀʙʟᴇ ɪɴ Hɪɴᴅɪ ❌",
                show_alert=True
            )
        else:
            await query.answer("Nᴏᴛ ᴀʟʟᴏᴡᴇᴅ — ʏᴏᴜ ᴀʀᴇ ɴᴏᴛ ᴛʜᴇ ʀᴇǫᴜᴇꜱᴛᴇʀ ❌", show_alert=True)

    elif query.data.startswith("nralert"):
        ident, from_user = query.data.split("#")  # Not Released
        if int(query.from_user.id) == int(from_user):
            user = await client.get_users(from_user)
            await query.answer(
                f"Hᴇʏ {user.first_name}, Tʜᴇ Mᴏᴠɪᴇ/ꜱʜᴏᴡ ɪꜱ Nᴏᴛ Rᴇʟᴇᴀꜱᴇᴅ Yᴇᴛ 🆕",
                show_alert=True
            )
        else:
            await query.answer("Yᴏᴜ ᴄᴀɴ'ᴛ ᴅᴏ ᴛʜɪꜱ ᴀꜱ ʏᴏᴜ ᴀʀᴇ ɴᴏᴛ ᴛʜᴇ ᴏʀɪɢɪɴᴀʟ ʀᴇǫᴜᴇꜱᴛᴇʀ ❌", show_alert=True)

    elif query.data.startswith("wsalert"):
        ident, from_user = query.data.split("#")  # Wrong Spelling
        if int(query.from_user.id) == int(from_user):
            user = await client.get_users(from_user)
            await query.answer(
                f"Hᴇʏ {user.first_name}, Yᴏᴜʀ Rᴇǫᴜᴇꜱᴛ ᴡᴀꜱ ʀᴇᴊᴇᴄᴛᴇᴅ ᴅᴜᴇ ᴛᴏ ᴡʀᴏɴɢ sᴘᴇʟʟɪɴɢ ❗",
                show_alert=True
            )
        else:
            await query.answer("Yᴏᴜ ᴅᴏɴ'ᴛ ʜᴀᴠᴇ ᴘᴇʀᴍɪssɪᴏɴ ᴛᴏ sᴇᴇ ᴛʜɪꜱ ❌", show_alert=True)

    elif DreamxData.startswith("generate_stream_link"):
        _, file_id = DreamxData.split(":")
        try:
            user_id = query.from_user.id
            username = query.from_user.mention
            log_msg = await client.send_cached_media(chat_id=BIN_CHANNEL, file_id=file_id,)
            fileName = {quote_plus(get_name(log_msg))}
            dreamx_stream = f"{URL}watch/{str(log_msg.id)}/{quote_plus(get_name(log_msg))}?hash={get_hash(log_msg)}"
            dreamx_download = f"{URL}{str(log_msg.id)}/{quote_plus(get_name(log_msg))}?hash={get_hash(log_msg)}"
            await query.answer(MSG_ALRT)
            await asyncio.sleep(1)
            await log_msg.reply_text(
                text=f"•• ʟɪɴᴋ ɢᴇɴᴇʀᴀᴛᴇᴅ ꜰᴏʀ ɪᴅ #{user_id} \n•• ᴜꜱᴇʀɴᴀᴍᴇ : {username} \n\n•• ᖴᎥᒪᗴ Nᗩᗰᗴ : {fileName}",
                quote=True,
                disable_web_page_preview=True,
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🚀 Fast Download 🚀", url=dreamx_download),  # we download Link
                                                    InlineKeyboardButton('🖥️ Watch online 🖥️', url=dreamx_stream)]])  # web stream Link
            )
            dreamcinezone = await query.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("🚀 Download ", url=dreamx_download),
                        InlineKeyboardButton('🖥️ Watch ', url=dreamx_stream)
                    ],
                    [
                        InlineKeyboardButton('📌 ᴊᴏɪɴ ᴜᴘᴅᴀᴛᴇꜱ ᴄʜᴀɴɴᴇʟ 📌', url=UPDATE_CHNL_LNK)
                    ]
                ])
            )
            await asyncio.sleep(DELETE_TIME)
            await dreamcinezone.delete()
            return
        except Exception as e:
            print(e)
            await query.answer(f"⚠️ SOMETHING WENT WRONG STREAM LINK  \n\n{e}", show_alert=True)
            return


    elif query.data == "prestream":
        await query.answer(text=script.PRE_STREAM_ALERT, show_alert=True)
        dreamcinezone = await client.send_photo(
            chat_id=query.message.chat.id,
            photo="https://i.ibb.co/whf8xF7j/photo-2025-07-26-10-42-46-7531339305176793100.jpg",
            caption=script.PRE_STREAM,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("🚀 Buy Premium 🚀", callback_data="premium_info")]
            ])
        )
        await asyncio.sleep(DELETE_TIME)
        await dreamcinezone.delete()


    elif query.data == "pagesn1":
        await query.answer(text=script.PAGE_TXT, show_alert=True)

    elif query.data == "sinfo":
        await query.answer(text=script.SINFO, show_alert=True)

    elif query.data == "start":
        from plugins.ui_pro import show_home
        return await show_home(query)

    elif query.data == "donation":
        buttons = [[
                InlineKeyboardButton('🌲 Sᴇɴᴅ Dᴏɴᴀᴛᴇ Sᴄʀᴇᴇɴsʜᴏᴛ Hᴇʀᴇ', url=OWNER_LNK)
            ],[
                InlineKeyboardButton('⇍ ʙᴀᴄᴋ ⇏', callback_data='about')
            ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.message.edit_text(text="● ◌ ◌")
        await query.message.edit_text(text="● ● ◌")
        await query.message.edit_text(text="● ● ●")
        reply_markup = InlineKeyboardMarkup(buttons)
        await client.edit_message_media(
            query.message.chat.id,
            query.message.id,
            InputMediaPhoto('https://graph.org/file/99eebf5dbe8a134f548e0.jpg')
        )
        await query.message.edit_text(
            text=script.DREAMXBOTZ_DONATION.format(query.from_user.mention, QR_CODE, OWNER_UPI_ID),
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )

    elif query.data == "help":
        query.data = "ui_help"
        from plugins.ui_pro import ui_help
        return await ui_help(client, query)

    elif query.data == "about":
        from plugins.ui_pro import ui_account
        return await ui_account(client, query)

    elif query.data == "give_trial":
        try:
            user_id = query.from_user.id
            has_free_trial = await db.check_trial_status(user_id)
            if has_free_trial:
                await query.answer(
                    "🚸 ʏᴏᴜ'ᴠᴇ ᴀʟʀᴇᴀᴅʏ ᴄʟᴀɪᴍᴇᴅ ʏᴏᴜʀ ꜰʀᴇᴇ ᴛʀɪᴀʟ ᴏɴᴄᴇ !\n\n📌 ᴄʜᴇᴄᴋᴏᴜᴛ ᴏᴜʀ ᴘʟᴀɴꜱ ʙʏ : /plan",
                    show_alert=True
                )
                return
            else:
                await db.give_free_trial(user_id)
                await query.answer("✅ Trial activated!", show_alert=True)

                msg = await client.send_photo(
                    chat_id=query.message.chat.id,
                    photo="https://i.ibb.co/0jC8MSDZ/photo-2025-07-26-10-42-36-7531339283701956616.jpg",
                    caption=(
                        "<b>🥳 ᴄᴏɴɢʀᴀᴛᴜʟᴀᴛɪᴏɴꜱ\n\n"
                        "🎉 ʏᴏᴜ ᴄᴀɴ ᴜsᴇ ꜰʀᴇᴇ ᴛʀᴀɪʟ ꜰᴏʀ <u>5 ᴍɪɴᴜᴛᴇs</u> ꜰʀᴏᴍ ɴᴏᴡ !\n\n"
                        "ɴᴇᴇᴅ ᴘʀᴇᴍɪᴜᴍ 👉🏻 /plan</b>"
                    ),
                    parse_mode=enums.ParseMode.HTML,
                    reply_markup=InlineKeyboardMarkup([[
                        InlineKeyboardButton("🚀 Buy Premium 🚀", callback_data="premium_info")
                    ]])
                )
                await asyncio.sleep(DELETE_TIME)
                return await msg.delete()
        except Exception as e:
            logging.exception("Error in give_trial callback")



    elif query.data == "source":
        buttons = [[
            InlineKeyboardButton('Owner', url=OWNER_LNK),
            InlineKeyboardButton('⇋ ʙᴀᴄᴋ ⇋', callback_data='about')
        ]]
        reply_markup = InlineKeyboardMarkup(buttons)
        await query.message.edit_text(
            text=script.SOURCE_TXT,
            reply_markup=reply_markup,
            parse_mode=enums.ParseMode.HTML
        )

    elif query.data == "ref_point":
        await query.answer(f'You Have: {referdb.get_refer_points(query.from_user.id)} Refferal points.', show_alert=True)

    elif query.data == "disclaimer":
            btn = [[
                    InlineKeyboardButton("⇋ ʙᴀᴄᴋ ⇋", callback_data="about")
                  ]]
            reply_markup = InlineKeyboardMarkup(btn)
            await query.message.edit_text(
                text=(script.DISCLAIMER_TXT),
                reply_markup=reply_markup,
                parse_mode=enums.ParseMode.HTML
            )

    elif query.data == "premium_info":
        try:
            btn = [[
                InlineKeyboardButton('• ʙᴜʏ ᴘʀᴇᴍɪᴜᴍ •', callback_data='buy_info'),
            ],[
                InlineKeyboardButton('• ʀᴇꜰᴇʀ ꜰʀɪᴇɴᴅꜱ', callback_data='reffff'),
                InlineKeyboardButton('ꜰʀᴇᴇ ᴛʀɪᴀʟ •', callback_data='give_trial')
            ],[
                InlineKeyboardButton('Home', callback_data='start')
            ]]
            reply_markup = InlineKeyboardMarkup(btn)
            await client.edit_message_media(
                chat_id=query.message.chat.id,
                message_id=query.message.id,
                media=InputMediaPhoto(media=SUBSCRIPTION, caption=script.BPREMIUM_TXT, parse_mode=enums.ParseMode.HTML),
                reply_markup=reply_markup
            )
        except Exception as e:
            logging.exception("Exception in 'premium_info' callback")


    elif query.data == "buy_info":
        try:
            btn = [[
                InlineKeyboardButton('ꜱᴛᴀʀ', callback_data='star_info'),
                InlineKeyboardButton('ᴜᴘɪ', callback_data='upi_info')
            ],[
                InlineKeyboardButton('⇋ ʙᴀᴄᴋ ᴛᴏ ᴘʀᴇᴍɪᴜᴍ ⇋', callback_data='premium_info')
            ]]
            reply_markup = InlineKeyboardMarkup(btn)
            await client.edit_message_media(
                chat_id=query.message.chat.id,
                message_id=query.message.id,
                media=InputMediaPhoto(media=SUBSCRIPTION, caption=script.PREMIUM_TEXT, parse_mode=enums.ParseMode.HTML),
                reply_markup=reply_markup
            )
        except Exception as e:
            logging.exception("Exception in 'buy_info' callback")

    elif query.data == "upi_info":
        try:
            btn = [[
                InlineKeyboardButton('• ꜱᴇɴᴅ  ᴘᴀʏᴍᴇɴᴛ ꜱᴄʀᴇᴇɴꜱʜᴏᴛ •', url=OWNER_LNK),
            ],[
                InlineKeyboardButton('⇋ ʙᴀᴄᴋ ⇋', callback_data='buy_info')
            ]]
            reply_markup = InlineKeyboardMarkup(btn)
            await client.edit_message_media(
                chat_id=query.message.chat.id,
                message_id=query.message.id,
                media=InputMediaPhoto(media=SUBSCRIPTION, caption=script.PREMIUM_UPI_TEXT.format(OWNER_UPI_ID), parse_mode=enums.ParseMode.HTML),
                reply_markup=reply_markup
            )
        except Exception as e:
            logging.exception("Exception in 'upi_info' callback")

    elif query.data == "star_info":
        try:
            btn = [
                InlineKeyboardButton(f"{stars}⭐", callback_data=f"buy_{stars}")
                for stars, days in STAR_PREMIUM_PLANS.items()
            ]
            buttons = [btn[i:i + 2] for i in range(0, len(btn), 2)]
            buttons.append([InlineKeyboardButton("‹ Back", callback_data="buy_info")])
            reply_markup = InlineKeyboardMarkup(buttons)
            await client.edit_message_media(
                chat_id=query.message.chat.id,
                message_id=query.message.id,
                media=InputMediaPhoto(media=SUBSCRIPTION, caption=script.PREMIUM_STAR_TEXT, parse_mode=enums.ParseMode.HTML),
                reply_markup=reply_markup
            )
        except Exception as e:
            logging.exception("Exception in 'star' callback")


    elif query.data.startswith("grp_pm"):
        _, grp_id = query.data.split("#")
        user_id = query.from_user.id if query.from_user else None
        if not await is_check_admin(client, int(grp_id), user_id):
            return await query.answer(script.NT_ADMIN_ALRT_TXT, show_alert=True)

        btn = await group_setting_buttons(int(grp_id))
        dreamx = await client.get_chat(int(grp_id))
        await query.message.edit(text=f"ᴄʜᴀɴɢᴇ ʏᴏᴜʀ ɢʀᴏᴜᴘ ꜱᴇᴛᴛɪɴɢꜱ ✅\nɢʀᴏᴜᴘ ɴᴀᴍᴇ - '{dreamx.title}'</b>⚙", reply_markup=InlineKeyboardMarkup(btn))

    elif query.data.startswith("removegrp"):
        user_id = query.from_user.id
        data = query.data
        grp_id = int(data.split("#")[1])
        if not await is_check_admin(client, grp_id, query.from_user.id):
            return await query.answer(script.NT_ADMIN_ALRT_TXT, show_alert=True)
        await db.remove_group_connection(grp_id, user_id)
        await query.answer("Group removed from your connections.", show_alert=True)
        connected_groups = await db.get_connected_grps(user_id)
        if not connected_groups:
            await query.edit_message_text("Nᴏ Cᴏɴɴᴇᴄᴛᴇᴅ Gʀᴏᴜᴘs Fᴏᴜɴᴅ .")
            return
        group_list = []
        for group in connected_groups:
            try:
                Chat = await client.get_chat(group)
                group_list.append([
                    InlineKeyboardButton(
                        text=Chat.title, callback_data=f"grp_pm#{Chat.id}")
                ])
            except Exception as e:
                print(f"Error In PM Settings Button - {e}")
                pass
        await query.edit_message_text(
            "⚠️ ꜱᴇʟᴇᴄᴛ ᴛʜᴇ ɢʀᴏᴜᴘ ᴡʜᴏꜱᴇ ꜱᴇᴛᴛɪɴɢꜱ ʏᴏᴜ ᴡᴀɴᴛ ᴛᴏ ᴄʜᴀɴɢᴇ.\n\n"
            "ɪꜰ ʏᴏᴜʀ ɢʀᴏᴜᴘ ɪꜱ ɴᴏᴛ ꜱʜᴏᴡɪɴɢ ʜᴇʀᴇ,\n"
            "ᴜꜱᴇ /reload ɪɴ ᴛʜᴀᴛ ɢʀᴏᴜᴘ ᴀɴᴅ ɪᴛ ᴡɪʟʟ ᴀᴘᴘᴇᴀʀ ʜᴇʀᴇ.",
            reply_markup=InlineKeyboardMarkup(group_list)
        )

    elif query.data.startswith("setgs"):
        ident, set_type, status, grp_id = query.data.split("#")
        userid = query.from_user.id if query.from_user else None
        if not await is_check_admin(client, int(grp_id), userid):
            await query.answer(script.NT_ADMIN_ALRT_TXT, show_alert=True)
            return
        if status == "True":
            await save_group_settings(int(grp_id), set_type, False)
            await query.answer("ᴏꜰꜰ ✗")
        else:
            await save_group_settings(int(grp_id), set_type, True)
            await query.answer("ᴏɴ ✓")
        settings = await get_settings(int(grp_id))
        if settings is not None:
            btn = await group_setting_buttons(int(grp_id))
            reply_markup = InlineKeyboardMarkup(btn)
            await query.message.edit_reply_markup(reply_markup)
    await query.answer(MSG_ALRT)


async def auto_filter(client, msg, spoll=False):
    """
    Core auto_filter logic with timing/debug logging removed.
    """
    curr_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()

    async def _schedule_delete(sent_obj, orig_msg, delay):
        try:
            await asyncio.sleep(delay)
            try:
                await sent_obj.delete()
            except Exception:
                pass
            try:
                await orig_msg.delete()
            except Exception:
                pass
        except Exception:
            # ignore scheduling errors
            pass

    # initialize to avoid NameError if reply_sticker fails
    m = None

    try:
        if not spoll:
            message = msg
            if message.text.startswith("/"):
                return
            if re.findall(r"((^\/|^,|^!|^\.|^[\U0001F600-\U000E007F]).*)", message.text):
                return
            if len(message.text) < 100:
                message_text = message.text or ""
                search = message_text.lower()

                stick_id = "CAACAgIAAxkBAAEPhm5o439f8A4sUGO2VcnBFZRRYxAxmQACtCMAAphLKUjeub7NKlvk2TYE"
                keyboard = InlineKeyboardMarkup(
                    [[InlineKeyboardButton(f'🔎 sᴇᴀʀᴄʜɪɴɢ {search}', callback_data="hiding")]]
                )
                try:
                    m = await message.reply_sticker(sticker=stick_id, reply_markup=keyboard)
                except Exception as e:
                    logger.exception("reply_sticker failed: %s", e)

                find = search.split(" ")
                search = ""
                removes = ["in", "upload", "series", "full",
                           "horror", "thriller", "mystery", "print", "file"]
                for x in find:
                    if x in removes:
                        continue
                    else:
                        search = search + x + " "
                search = re.sub(r"\b(pl(i|e)*?(s|z+|ease|se|ese|(e+)s(e)?)|((send|snd|giv(e)?|gib)(\sme)?)|movie(s)?|new|latest|bro|bruh|broh|helo|that|find|dubbed|link|venum|iruka|pannunga|pannungga|anuppunga|anupunga|anuppungga|anupungga|film|undo|kitti|kitty|tharu|kittumo|kittum|movie|any(one)|with\ssubtitle(s)?)", "", search, flags=re.IGNORECASE)
                search = re.sub(r"\s+", " ", search).strip()
                search = search.replace("-", " ")
                search = search.replace(":", "")

                files, offset, total_results = await get_search_results(message.chat.id, search, offset=0, filter=True)

                settings = await get_settings(message.chat.id)
                if not files:
                    if settings.get("spell_check"):
                        ai_sts = await m.edit('🤖 ᴘʟᴇᴀꜱᴇ ᴡᴀɪᴛ, ᴀɪ ɪꜱ ᴄʜᴇᴄᴋɪɴɢ ʏᴏᴜʀ ꜱᴘᴇʟʟɪɴɢ...')
                        is_misspelled = await ai_spell_check(chat_id=message.chat.id, wrong_name=search)

                        if is_misspelled:
                            await ai_sts.edit(f'✅ Aɪ Sᴜɢɢᴇsᴛᴇᴅ: <code>{is_misspelled}</code>\n🔍 Searching for it...')
                            message.text = is_misspelled
                            await ai_sts.delete()
                            return await auto_filter(client, message)
                        await ai_sts.delete()
                        result = await advantage_spell_chok(client, message)
                        return result
                    else:
                        try:
                            if m:
                                await m.delete()
                        except Exception:
                            pass
                        result = await advantage_spell_chok(client, message)
                        return result
            else:
                return
        else:
            # spoll branch
            message = msg.message.reply_to_message
            search, files, offset, total_results = spoll
            m = await message.reply_text(f'🔎 sᴇᴀʀᴄʜɪɴɢ {search}', reply_to_message_id=message.id)
            settings = await get_settings(message.chat.id)
            await msg.message.delete()

        key = f"{message.chat.id}-{message.id}"
        FRESH[key] = search
        temp.GETALL[key] = files
        temp.SHORT[message.from_user.id] = message.chat.id

        if settings.get('button'):
            groups, group_next_offset = await _pro_discover_groups(message.chat.id, search, files, offset)
            PRO_SEARCH[key] = {
                "key": key,
                "groups": groups,
                "title_page": 0,
                "caption": None,
                "query": search,
                "chat_id": message.chat.id,
                "user_id": message.from_user.id if message.from_user else 0,
                "group_next_offset": group_next_offset,
                "total_results": total_results,
                "meta": None,
            }

        if settings.get('button'):
            btn = [
                [
                    InlineKeyboardButton(text=f"🔗 {get_size(file.file_size)} ≽ " + clean_filename(
                        file.file_name), callback_data=f'file#{file.file_id}'),
                ]
                for file in files
            ]
            btn.insert(0,
                       [
                           InlineKeyboardButton(
                               "✦ Quality", callback_data=f"qualities#{key}"),
                           InlineKeyboardButton(
                               "✦ Language", callback_data=f"languages#{key}"),
                           InlineKeyboardButton(
                               "✦ Season",  callback_data=f"seasons#{key}")
                       ]
                       )
            btn.insert(0,
                       [
                           InlineKeyboardButton(
                               "✦ Premium", url=f"https://t.me/{temp.U_NAME}?start=premium"),
                           InlineKeyboardButton(
                               "✦ Send All", callback_data=f"sendfiles#{key}")

                       ])
        else:
            btn = []
            btn.insert(0,
                       [
                           InlineKeyboardButton(
                               "✦ Quality", callback_data=f"qualities#{key}"),
                           InlineKeyboardButton(
                               "✦ Language", callback_data=f"languages#{key}"),
                           InlineKeyboardButton(
                               "✦ Season",  callback_data=f"seasons#{key}")
                       ]
                       )
            btn.insert(0,
                       [
                           InlineKeyboardButton(
                               "✦ Premium", url=f"https://t.me/{temp.U_NAME}?start=premium"),
                           InlineKeyboardButton(
                               "✦ Send All", callback_data=f"sendfiles#{key}")
                       ])

        if offset != "":
            req = message.from_user.id if message.from_user else 0
            if ULTRA_FAST_MODE:
                btn.append(
                    [InlineKeyboardButton("Page", callback_data="pages"), InlineKeyboardButton(
                        text="1", callback_data="pages"), InlineKeyboardButton(text="Next ›", callback_data=f"next_{req}_{key}_{offset}")]
                )
            else:
                try:
                    if settings['max_btn']:
                        btn.append(
                            [InlineKeyboardButton("Page", callback_data="pages"), InlineKeyboardButton(
                                text=f"1/{math.ceil(int(total_results)/10)}", callback_data="pages"), InlineKeyboardButton(text="Next ›", callback_data=f"next_{req}_{key}_{offset}")]
                        )
                    else:
                        btn.append(
                            [InlineKeyboardButton("Page", callback_data="pages"), InlineKeyboardButton(
                                text=f"1/{math.ceil(int(total_results)/int(MAX_B_TN))}", callback_data="pages"), InlineKeyboardButton(text="Next ›", callback_data=f"next_{req}_{key}_{offset}")]
                        )
                except KeyError:
                    await save_group_settings(message.chat.id, 'max_btn', True)
                    btn.append(
                        [InlineKeyboardButton("Page", callback_data="pages"), InlineKeyboardButton(
                            text=f"1/{math.ceil(int(total_results)/10)}", callback_data="pages"), InlineKeyboardButton(text="Next ›", callback_data=f"next_{req}_{key}_{offset}")]
                    )
        else:
            btn.append([InlineKeyboardButton(
                text="↭ ɴᴏ ᴍᴏʀᴇ ᴘᴀɢᴇꜱ ᴀᴠᴀɪʟᴀʙʟᴇ ↭", callback_data="pages")])

        if settings.get('imdb'):
            imdb = await get_posterx(search, file=(files[0]).file_name) if TMDB_POSTER else await get_poster(search, file=(files[0]).file_name)
        else:
            imdb = None

        cur_time = datetime.now(pytz.timezone('Asia/Kolkata')).time()
        time_difference = timedelta(hours=cur_time.hour, minutes=cur_time.minute, seconds=(cur_time.second+(cur_time.microsecond/1000000))) - \
            timedelta(hours=curr_time.hour, minutes=curr_time.minute,
                      seconds=(curr_time.second+(curr_time.microsecond/1000000)))
        remaining_seconds = "{:.2f}".format(time_difference.total_seconds())

        TEMPLATE = script.IMDB_TEMPLATE_TXT
        settings = await get_settings(message.chat.id)
        if settings.get('template'):
            TEMPLATE = settings['template']

        if imdb:
            combined_text = " ".join(
                f"{f.file_name} {getattr(f, 'caption', '') or ''}" for f in files
            )
            detected_language = extract_language(combined_text)
            cap = TEMPLATE.format(
                query=search,
                title=imdb['title'],
                votes=imdb['votes'],
                aka=imdb["aka"],
                seasons=imdb["seasons"],
                box_office=imdb['box_office'],
                localized_title=imdb['localized_title'],
                kind=imdb['kind'],
                imdb_id=imdb["imdb_id"],
                cast=imdb['cast'],
                runtime=imdb['runtime'],
                countries=imdb['countries'],
                certificates=imdb['certificates'],
                languages=detected_language if detected_language != "Nᴏᴛ Aᴠᴀɪʟᴀʙʟᴇ" else imdb['languages'],
                director=imdb['director'],
                writer=imdb['writer'],
                producer=imdb['producer'],
                composer=imdb['composer'],
                cinematographer=imdb['cinematographer'],
                music_team=imdb['music_team'],
                distributors=imdb['distributors'],
                release_date=imdb['release_date'],
                year=imdb['year'],
                genres=imdb['genres'],
                poster=imdb['poster'],
                plot=imdb['plot'] if settings.get('button') else "N/A",
                rating=imdb['rating'],
                url=imdb['url'],
                grp_lnk=BACKUP_CHANNEL_LINK,
                **locals()
            )
            temp.IMDB_CAP[message.from_user.id] = cap
            if not settings.get('button'):
                cap += "\n\n<b><u>Your Requested Files Are Here</u></b>\n\n"
                for idx, file in enumerate(files, start=1):
                    cap += f"<b>\n{idx}. <a href='https://telegram.me/{temp.U_NAME}?start=file_{message.chat.id}_{file.file_id}'>[{get_size(file.file_size)}] {clean_filename(file.file_name)}\n</a></b>"
        else:
            temp.IMDB_CAP[message.from_user.id] = None
            if ULTRA_FAST_MODE:
                if settings.get('button'):
                    cap = f"<b>🏷 ᴛɪᴛʟᴇ : <code>{search}</code>\n⏰ ʀᴇsᴜʟᴛ ɪɴ : <code>{remaining_seconds} Sᴇᴄᴏɴᴅs</code>\n\n📝 ʀᴇǫᴜᴇsᴛᴇᴅ ʙʏ : {message.from_user.mention}\n⚜️ ᴘᴏᴡᴇʀᴇᴅ ʙʏ : ⚡ {message.chat.title or temp.B_LINK or 'iP Update'} \n\n<u>Your Requested Files Are Here</u> \n\n</b>"
                else:
                    cap = f"<b>🏷 ᴛɪᴛʟᴇ : <code>{search}</code>\n⏰ ʀᴇsᴜʟᴛ ɪɴ : <code>{remaining_seconds} Sᴇᴄᴏɴᴅs</code>\n\n📝 ʀᴇǫᴜᴇsᴛᴇᴅ ʙʏ : {message.from_user.mention}\n⚜️ ᴘᴏᴡᴇʀᴇᴅ ʙʏ : ⚡ {message.chat.title or temp.B_LINK or 'iP Update'} \n\n<u>Your Requested Files Are Here</u> \n\n</b>"
                    for idx, file in enumerate(files, start=1):
                        cap += f"<b>\n{idx}. <a href='https://telegram.me/{temp.U_NAME}?start=file_{message.chat.id}_{file.file_id}'>[{get_size(file.file_size)}] {clean_filename(file.file_name)}\n</a></b>"
            else:
                if settings.get('button'):
                    cap = f"<b>🏷 ᴛɪᴛʟᴇ : <code>{search}</code>\n🧱 ᴛᴏᴛᴀʟ ꜰɪʟᴇꜱ : <code>{total_results}</code>\n⏰ ʀᴇsᴜʟᴛ ɪɴ : <code>{remaining_seconds} Sᴇᴄᴏɴᴅs</code>\n\n📝 ʀᴇǫᴜᴇsᴛᴇᴅ ʙʏ : {message.from_user.mention}\n⚜️ ᴘᴏᴡᴇʀᴇᴅ ʙʏ : ⚡ {message.chat.title or temp.B_LINK or 'iP Update'} \n\n<u>Your Requested Files Are Here</u> \n\n</b>"
                else:
                    cap = f"<b>🏷 ᴛɪᴛʟᴇ : <code>{search}</code>\n🧱 ᴛᴏᴛᴀʟ ꜰɪʟᴇꜱ : <code>{total_results}</code>\n⏰ ʀᴇsᴜʟᴛ ɪɴ : <code>{remaining_seconds} Sᴇᴄᴏɴᴅs</code>\n\n📝 ʀᴇǫᴜᴇsᴛᴇᴅ ʙʏ : {message.from_user.mention}\n⚜️ ᴘᴏᴡᴇʀᴇᴅ ʙʏ : ⚡ {message.chat.title or temp.B_LINK or 'iP Update'} \n\n<u>Your Requested Files Are Here</u> \n\n</b>"

                    for idx, file in enumerate(files, start=1):
                        cap += f"<b>\n{idx}. <a href='https://telegram.me/{temp.U_NAME}?start=file_{message.chat.id}_{file.file_id}'>[{get_size(file.file_size)}] {clean_filename(file.file_name)}\n</a></b>"

        # Phase-3 title-first UI: clean text-only search page.
        is_pro_search = settings.get('button') and key in PRO_SEARCH
        if is_pro_search:
            PRO_SEARCH[key]["meta"] = imdb or {}
            PRO_SEARCH[key]["caption"] = _pro_search_caption(PRO_SEARCH[key])
            cap = PRO_SEARCH[key]["caption"]
            btn = _pro_search_markup(PRO_SEARCH[key])

        sent = None
        try:
            if imdb and imdb.get('poster'):
                try:
                    if TMDB_POSTER:
                        photo = imdb.get('backdrop') if imdb.get('backdrop') and LANDSCAPE_POSTER else imdb.get('poster')
                    else:
                        photo = imdb.get('poster')
                    sent = await message.reply_photo(photo=photo, caption=cap, reply_markup=btn if isinstance(btn, InlineKeyboardMarkup) else InlineKeyboardMarkup(btn), parse_mode=enums.ParseMode.HTML)
                    if m:
                        await m.delete()
                except (MediaEmpty, PhotoInvalidDimensions, WebpageMediaEmpty):
                    pic = imdb.get('poster')
                    poster = pic.replace('.jpg', "._V1_UX360.jpg")
                    sent = await message.reply_photo(photo=poster, caption=cap, reply_markup=btn if isinstance(btn, InlineKeyboardMarkup) else InlineKeyboardMarkup(btn), parse_mode=enums.ParseMode.HTML)
                    if m:
                        await m.delete()
                except Exception as e:
                    logger.exception(e)
                    sent = await message.reply_text(text=cap, reply_markup=btn if isinstance(btn, InlineKeyboardMarkup) else InlineKeyboardMarkup(btn), disable_web_page_preview=True, parse_mode=enums.ParseMode.HTML)
            else:
                sent = await message.reply_text(text=cap, reply_markup=btn if isinstance(btn, InlineKeyboardMarkup) else InlineKeyboardMarkup(btn), disable_web_page_preview=True, parse_mode=enums.ParseMode.HTML)
                if m:
                    await m.delete()
        except Exception as e:
            logger.exception("Failed to send result: %s", e)
            return

        try:
            if settings.get('auto_delete'):
                asyncio.create_task(_schedule_delete(sent, message, DELETE_TIME))
        except KeyError:
            try:
                await save_group_settings(message.chat.id, 'auto_delete', True)
            except Exception:
                pass
            asyncio.create_task(_schedule_delete(sent, message, DELETE_TIME))
        return

    except Exception as e:
        logger.exception(e)
        return

async def ai_spell_check(chat_id, wrong_name):
    async def search_movie(wrong_name):
        search_results = imdb.search_movie(wrong_name)
        movie_list = [movie['title'] for movie in search_results]
        return movie_list
    movie_list = await search_movie(wrong_name)
    if not movie_list:
        return
    for _ in range(5):
        closest_match = process.extractOne(wrong_name, movie_list)
        if not closest_match or closest_match[1] <= 80:
            return
        movie = closest_match[0]
        files, _, _ = await get_search_results(chat_id=chat_id, query=movie)
        if files:
            return movie
        movie_list.remove(movie)


async def advantage_spell_chok(client, message):
    mv_id = message.id
    search = message.text
    chat_id = message.chat.id
    settings = await get_settings(chat_id)
    query = re.sub(
        r"\b(pl(i|e)*?(s|z+|ease|se|ese|(e+)s(e)?)|((send|snd|giv(e)?|gib)(\sme)?)|movie(s)?|new|latest|^h(e|a)?(l)*(o)*|mal(ayalam)?|t(h)?amil|file|that|find|und(o)*|kit(t(i|y)?)?o(w)?|thar(u)?(o)*w?|kittum(o)*|aya(k)*(um(o)*)?|full\smovie|any(one)|with\ssubtitle(s)?)",
        "", message.text, flags=re.IGNORECASE)
    query = query.strip() + " movie"
    try:
        movies = await get_poster(search, bulk=True)
    except:
        k = await message.reply(script.I_CUDNT.format(message.from_user.mention))
        await asyncio.sleep(60)
        await k.delete()
        try:
            await message.delete()
        except:
            pass
        return
    if not movies:
        google = search.replace(" ", "+")
        button = [[InlineKeyboardButton(
            "🔍 ᴄʜᴇᴄᴋ sᴘᴇʟʟɪɴɢ ᴏɴ ɢᴏᴏɢʟᴇ 🔍", url=f"https://www.google.com/search?q={google}")]]
        k = await message.reply_text(text=script.I_CUDNT.format(search), reply_markup=InlineKeyboardMarkup(button))

        req_key = f"{chat_id}-{mv_id}"
        OWNER_REQ_CACHE[req_key] = {
            "query": search,
            "user_id": message.from_user.id if message.from_user else 0,
            "mention": message.from_user.mention if message.from_user else "User",
        }
        req_button = [[InlineKeyboardButton(
            "📩 Request Owner", callback_data=f"reqowner#{req_key}")]]
        r = await message.reply_text(text=script.REQUEST_OWNER_TXT, reply_markup=InlineKeyboardMarkup(req_button))

        await asyncio.sleep(60)
        await k.delete()
        try:
            await r.delete()
        except:
            pass
        OWNER_REQ_CACHE.pop(req_key, None)
        try:
            await message.delete()
        except:
            pass
        return
    user = message.from_user.id if message.from_user else 0
    buttons = [
        [InlineKeyboardButton(text=movie.get('title'), callback_data=f"spol#{movie.movieID}#{user}")
         ] for movie in movies]

    buttons.append([InlineKeyboardButton(
        text="🚫 ᴄʟᴏsᴇ 🚫", callback_data='close_data')])
    d = await message.reply_text(text=script.CUDNT_FND.format(message.from_user.mention), reply_markup=InlineKeyboardMarkup(buttons), reply_to_message_id=message.id)
    await asyncio.sleep(60)
    await d.delete()
    try:
        await message.delete()
    except:
        pass
