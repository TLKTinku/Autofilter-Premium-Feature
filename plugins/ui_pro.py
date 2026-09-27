from pyrogram import Client, filters, StopPropagation
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from info import UPDATE_CHNL_LNK, GRP_LNK, PICS, OWNER_LNK, OWNER_UPI_ID, STAR_PREMIUM_PLANS
from utils import temp
from database.users_chats_db import db
import random
import datetime
import pytz


_BRAND = "MY MOVIES"


def _safe_url(value, fallback="https://t.me"):
    """Return only a Telegram/HTTP URL suitable for InlineKeyboardButton.url."""
    value = str(value or "").strip()
    if value.startswith(("https://", "http://")) and " " not in value:
        return value
    return fallback


OWNER_URL = _safe_url(OWNER_LNK, _safe_url(GRP_LNK))
UPDATE_URL = _safe_url(UPDATE_CHNL_LNK, _safe_url(GRP_LNK))


def _btn(text, callback_data=None, url=None):
    return InlineKeyboardButton(text, callback_data=callback_data, url=url)


def home_kb():
    return InlineKeyboardMarkup([
        [_btn("🔎  Search Movies", callback_data="ui_search")],
        [
            _btn("🔥  Trending", callback_data="topsearch"),
            _btn("🆕  Latest", url=UPDATE_URL),
        ],
        [
            _btn("👤  My Account", callback_data="ui_account"),
            _btn("📨  Request", callback_data="ui_request"),
        ],
        [
            _btn("💎  Premium", callback_data="premium_info"),
            _btn("⚙️  Settings", callback_data="ui_settings"),
        ],
        [_btn("❓  Help", callback_data="ui_help"), _btn("ℹ️  About", callback_data="ui_about")],
    ])


def nav_kb(*rows):
    out = list(rows)
    out.append([
        _btn("⬅  Back", callback_data="ui_home"),
        _btn("🏠  Home", callback_data="ui_home"),
    ])
    return InlineKeyboardMarkup(out)


def home_text(name="there"):
    return (
        "╭────────────────────────╮\n"
        "│      🎬 <b>MY MOVIES</b>      │\n"
        "│   MOVIES • SERIES • ANIME  │\n"
        "╰────────────────────────╯\n\n"
        f"👋 <b>Welcome, {name}</b>\n\n"
        "🔎 Search a title and find the files you need.\n"
        "🎬 Open movie details, quality and language filters.\n"
        "💎 Premium unlocks the premium experience.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "        <b>EXPLORE</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )


async def _edit(query, text, markup):
    """Edit the current UI message in-place; never create duplicate navigation messages."""
    msg = query.message
    try:
        if getattr(msg, "photo", None) or getattr(msg, "video", None) or getattr(msg, "animation", None):
            await msg.edit_caption(caption=text, reply_markup=markup, parse_mode="html")
        else:
            await msg.edit_text(text=text, reply_markup=markup, disable_web_page_preview=True, parse_mode="html")
        await query.answer()
        raise StopPropagation
    except Exception as exc:
        # Do not silently create a second message. Show a compact callback error.
        try:
            await query.answer("⚠️ This screen could not be updated. Please tap again.", show_alert=True)
        except Exception:
            pass


def premium_overview_text():
    return (
        "╭────────────────────────╮\n"
        "│      💎 <b>PREMIUM</b>         │\n"
        "╰────────────────────────╯\n\n"
        "<b>MY MOVIES PRO</b>\n"
        "A smoother, premium-first experience.\n\n"
        "✓ Less verification friction\n"
        "✓ Direct file access where supported\n"
        "✓ Premium streaming features\n"
        "✓ Priority support / requests\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Choose an option below."
    )


def premium_overview_kb():
    return InlineKeyboardMarkup([
        [_btn("💳  View Plans", callback_data="buy_info")],
        [_btn("🎁  Refer Friends", callback_data="reffff"), _btn("🎟  Free Trial", callback_data="give_trial")],
        [_btn("👤  My Plan", callback_data="ui_myplan")],
        [_btn("⬅  Back", callback_data="ui_home"), _btn("🏠  Home", callback_data="ui_home")],
    ])


def premium_plans_text():
    lines = [
        "╭────────────────────────╮",
        "│       💳 <b>PLANS</b>         │",
        "╰────────────────────────╯",
        "",
        "Choose a Telegram Stars plan or UPI payment:",
        "",
    ]
    for amount, duration in STAR_PREMIUM_PLANS.items():
        lines.append(f"⭐ <b>{amount} Stars</b>  •  {duration}")
    lines.extend(["", "━━━━━━━━━━━━━━━━━━━━━━", "💳 UPI payment is available below."])
    return "\n".join(lines)


def premium_plans_kb():
    rows = []
    items = list(STAR_PREMIUM_PLANS.items())
    for i in range(0, len(items), 2):
        row = [_btn(f"⭐ {items[i][0]} • {items[i][1]}", callback_data=f"buy_{items[i][0]}")]
        if i + 1 < len(items):
            row.append(_btn(f"⭐ {items[i+1][0]} • {items[i+1][1]}", callback_data=f"buy_{items[i+1][0]}"))
        rows.append(row)
    rows.append([_btn("💳  UPI Payment", callback_data="upi_info")])
    rows.append([_btn("⬅  Back to Premium", callback_data="premium_info")])
    return InlineKeyboardMarkup(rows)


def premium_upi_text():
    return (
        "╭────────────────────────╮\n"
        "│       💳 <b>UPI</b>          │\n"
        "╰────────────────────────╯\n\n"
        "Pay using UPI / supported bank payment.\n\n"
        f"💳 <b>UPI ID</b>\n<code>{OWNER_UPI_ID}</code>\n\n"
        "⚠️ After payment, send the payment screenshot to the owner.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )


def premium_upi_kb():
    return InlineKeyboardMarkup([
        [_btn("📸  Send Payment Screenshot", url=OWNER_URL)],
        [_btn("⬅  Back to Plans", callback_data="buy_info")],
    ])


async def show_myplan(query):
    user_id = query.from_user.id
    user = query.from_user.mention
    data = await db.get_user(user_id)
    if data and data.get("expiry_time"):
        expiry = data["expiry_time"].astimezone(pytz.timezone("Asia/Kolkata"))
        now = datetime.datetime.now(pytz.timezone("Asia/Kolkata"))
        remaining = expiry - now
        if remaining.total_seconds() <= 0:
            active = False
        else:
            active = True
        if active:
            total_seconds = int(remaining.total_seconds())
            days, rem = divmod(total_seconds, 86400)
            hours, rem = divmod(rem, 3600)
            minutes = rem // 60
            status = "● ACTIVE"
            left = f"{days}d  {hours}h  {minutes}m"
            expiry_text = expiry.strftime("%d %b %Y  •  %I:%M %p")
        else:
            status = "● EXPIRED"
            left = "Expired"
            expiry_text = expiry.strftime("%d %b %Y  •  %I:%M %p")
    else:
        active = False
        status = "● FREE"
        left = "No active premium plan"
        expiry_text = "—"

    text = (
        "╭────────────────────────╮\n"
        "│      👤 <b>MY PLAN</b>        │\n"
        "╰────────────────────────╯\n\n"
        f"👤 {user}\n"
        f"🆔 <code>{user_id}</code>\n\n"
        f"💎 <b>Status</b>\n{status}\n\n"
        f"⏳ <b>Time Left</b>\n{left}\n\n"
        f"📅 <b>Expiry</b>\n{expiry_text}\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━"
    )
    kb = InlineKeyboardMarkup([
        [_btn("💎  Extend Plan", callback_data="premium_info")],
        [_btn("⬅  Back", callback_data="ui_home"), _btn("🏠  Home", callback_data="ui_home")],
    ])
    await _edit(query, text, kb)


async def show_home(query):
    text = home_text(query.from_user.mention)
    markup = home_kb()
    try:
        if getattr(query.message, "photo", None) and PICS:
            await query.message.edit_media(
                InputMediaPhoto(media=PICS[0], caption=text, parse_mode="html"),
                reply_markup=markup,
            )
            await query.answer()
            raise StopPropagation
    except Exception:
        pass
    await _edit(query, text, markup)


@Client.on_callback_query(filters.regex(r"^ui_home$"), group=-100)
async def ui_home(_, query):
    await show_home(query)


@Client.on_callback_query(filters.regex(r"^ui_search$"), group=-100)
async def ui_search(_, query):
    await _edit(
        query,
        "╭────────────────────────╮\n"
        "│       🔎 <b>SEARCH</b>        │\n"
        "╰────────────────────────╯\n\n"
        "Send the name of a movie, series or anime in this chat.\n\n"
        "<b>Examples</b>\n"
        "• <code>Avengers</code>\n"
        "• <code>Interstellar 2014</code>\n"
        "• <code>Naruto S01</code>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 <b>Tip:</b> Add the year for movies or S01/S02 for series.",
        nav_kb(),
    )


@Client.on_callback_query(filters.regex(r"^ui_help$"), group=-100)
async def ui_help(_, query):
    await _edit(
        query,
        "╭────────────────────────╮\n"
        "│      ❓ <b>HELP CENTER</b>     │\n"
        "╰────────────────────────╯\n\n"
        "🔎 <b>Search</b>\n"
        "Send a movie, series or anime name.\n\n"
        "🎞 <b>Choose Files</b>\n"
        "Open a title, then choose quality, language or season.\n\n"
        "📨 <b>Request</b>\n"
        "Can't find a title? Send a request.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Need more help? Contact support below.",
        nav_kb(
            [_btn("🔎  Search Guide", callback_data="ui_h_search")],
            [_btn("📨  Request Guide", callback_data="ui_request")],
            [_btn("🆘  Support", url=_safe_url(GRP_LNK, UPDATE_URL))],
        ),
    )


@Client.on_callback_query(filters.regex(r"^ui_h_search$"), group=-100)
async def ui_h_search(_, query):
    await _edit(
        query,
        "╭────────────────────────╮\n"
        "│      🔎 <b>SEARCH GUIDE</b>    │\n"
        "╰────────────────────────╯\n\n"
        "1️⃣ Type the title.\n"
        "2️⃣ Select the correct movie or series.\n"
        "3️⃣ Open its details.\n"
        "4️⃣ Pick quality, language or season.\n"
        "5️⃣ Tap the file you want.\n\n"
        "💡 <b>Best results:</b> Movie + year, or Series + S01.",
        nav_kb(),
    )


@Client.on_callback_query(filters.regex(r"^ui_account$"), group=-100)
async def ui_account(_, query):
    u = query.from_user
    await _edit(
        query,
        "╭────────────────────────╮\n"
        "│     👤 <b>MY ACCOUNT</b>      │\n"
        "╰────────────────────────╯\n\n"
        f"👤 <b>Name</b>\n{u.mention}\n\n"
        f"🆔 <b>User ID</b>\n<code>{u.id}</code>\n\n"
        "💎 <b>Plan</b>\nTap My Plan below to check your current premium status.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━",
        nav_kb(
            [_btn("💎  Premium", callback_data="premium_info")],
            [_btn("📋  My Plan", callback_data="ui_myplan")],
        ),
    )


@Client.on_callback_query(filters.regex(r"^ui_request$"), group=-100)
async def ui_request(_, query):
    await _edit(
        query,
        "╭────────────────────────╮\n"
        "│    📨 <b>REQUEST MOVIE</b>    │\n"
        "╰────────────────────────╯\n\n"
        "Can't find what you're looking for?\n\n"
        "Send the title in the movie group.\n"
        "For better results, include:\n"
        "📅 Year\n"
        "🌐 Language\n"
        "📺 Season / Episode\n\n"
        "Example: <code>Interstellar 2014</code>",
        nav_kb(
            [_btn("📨  Open Request Group", url=_safe_url(GRP_LNK, UPDATE_URL))],
        ),
    )


@Client.on_callback_query(filters.regex(r"^ui_about$"), group=-100)
async def ui_about(_, query):
    await _edit(
        query,
        "╭────────────────────────╮\n"
        "│       ℹ️ <b>ABOUT</b>          │\n"
        "╰────────────────────────╯\n\n"
        f"🎬 <b>{temp.B_NAME or 'MY MOVIES'}</b>\n"
        "A movie, series and anime search provider.\n\n"
        "⚡ Fast search\n"
        "🎞 Quality / language filters\n"
        "📺 Season-aware series browsing\n"
        "💎 Premium features\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "Built for a clean, simple Telegram experience.",
        nav_kb(
            [_btn("👤  Owner", url=OWNER_URL)],
            [_btn("📢  Updates", url=UPDATE_URL)],
        ),
    )


@Client.on_callback_query(filters.regex(r"^ui_settings$"), group=-100)
async def ui_settings(_, query):
    await _edit(
        query,
        "╭────────────────────────╮\n"
        "│     ⚙️ <b>SETTINGS</b>        │\n"
        "╰────────────────────────╯\n\n"
        "Your group search preferences are managed with the\n"
        "existing <code>/settings</code> command.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "💎 Premium options are available from the Premium menu.",
        nav_kb(
            [_btn("💎  Premium", callback_data="premium_info")],
        ),
    )


@Client.on_callback_query(filters.regex(r"^ui_myplan$"), group=-100)
async def ui_myplan(_, query):
    await show_myplan(query)


async def _show_premium_screen(query, mode="overview"):
    from info import SUBSCRIPTION
    if mode == "overview":
        text, markup = premium_overview_text(), premium_overview_kb()
    elif mode == "plans":
        text, markup = premium_plans_text(), premium_plans_kb()
    else:
        text, markup = premium_upi_text(), premium_upi_kb()
    try:
        if getattr(query.message, "photo", None):
            await query.message.edit_media(
                InputMediaPhoto(media=SUBSCRIPTION, caption=text, parse_mode="html"),
                reply_markup=markup,
            )
        else:
            await query.message.edit_text(text=text, reply_markup=markup, parse_mode="html")
        await query.answer()
        raise StopPropagation
    except Exception:
        try:
            await query.answer("⚠️ This screen could not be updated. Please tap again.", show_alert=True)
        except Exception:
            pass


@Client.on_callback_query(filters.regex(r"^premium_info$"), group=-100)
async def ui_premium_info(_, query):
    await _show_premium_screen(query, "overview")


@Client.on_callback_query(filters.regex(r"^buy_info$"), group=-100)
async def ui_buy_info(_, query):
    await _show_premium_screen(query, "plans")


@Client.on_callback_query(filters.regex(r"^upi_info$"), group=-100)
async def ui_upi_info(_, query):
    await _show_premium_screen(query, "upi")


async def dispatch_ui(query):
    data = query.data or ""
    handlers = {
        "ui_home": show_home,
        "ui_search": ui_search,
        "ui_help": ui_help,
        "ui_h_search": ui_h_search,
        "ui_account": ui_account,
        "ui_request": ui_request,
        "ui_settings": ui_settings,
        "ui_about": ui_about,
        "ui_myplan": ui_myplan,
    }
    handler = handlers.get(data)
    if handler:
        await handler(None, query)
    else:
        try:
            await query.answer()
        except Exception:
            pass
