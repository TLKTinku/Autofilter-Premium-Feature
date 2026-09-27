from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from info import UPDATE_CHNL_LNK, GRP_LNK, PICS, OWNER_LNK, OWNER_UPI_ID
from utils import temp
from database.users_chats_db import db
import random
import datetime
import pytz


_BRAND = "MY MOVIES"


def _btn(text, callback_data=None, url=None):
    return InlineKeyboardButton(text, callback_data=callback_data, url=url)


def home_kb():
    return InlineKeyboardMarkup([
        [_btn("🔎  Search Movies", callback_data="ui_search")],
        [
            _btn("🔥  Trending", callback_data="topsearch"),
            _btn("🆕  Latest", url=UPDATE_CHNL_LNK or "https://t.me"),
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
    msg = query.message
    # Preserve the current media where possible. Replacing it on every click
    # causes flicker and can fail on Telegram's media editing rules.
    try:
        if getattr(msg, "photo", None) or getattr(msg, "video", None) or getattr(msg, "animation", None):
            await msg.edit_caption(caption=text, reply_markup=markup)
        else:
            await msg.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
        await query.answer()
        return
    except Exception:
        pass

    # If the old message cannot be edited into the new screen, create a fresh
    # message rather than silently doing nothing.
    try:
        pic = random.choice(PICS) if PICS else None
        if pic:
            await query.message.reply_photo(photo=pic, caption=text, reply_markup=markup)
        else:
            await query.message.reply_text(text, reply_markup=markup, disable_web_page_preview=True)
    except Exception:
        pass
    try:
        await query.answer()
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
    from info import STAR_PREMIUM_PLANS
    lines = [
        "╭────────────────────────╮",
        "│       💳 <b>PLANS</b>         │",
        "╰────────────────────────╯",
        "",
        "Choose a Telegram Stars plan:",
        "",
    ]
    for amount, duration in STAR_PREMIUM_PLANS.items():
        lines.append(f"⭐ <b>{amount} Stars</b>  •  {duration}")
    lines.extend([
        "",
        "━━━━━━━━━━━━━━━━━━━━━━",
        "UPI is also available for manual payment.",
    ])
    return "\n".join(lines)


def premium_plans_kb():
    from info import STAR_PREMIUM_PLANS
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
        [_btn("📸  Send Payment Screenshot", url=OWNER_LNK)],
        [_btn("⬅  Back to Plans", callback_data="buy_info")],
    ])


async def show_myplan(query):
    """Render the account plan screen without depending on the /myplan command UI."""
    user_id = query.from_user.id
    user = query.from_user.mention
    try:
        data = await db.get_user(user_id)
    except Exception:
        data = None

    status = "● FREE"
    left = "No active premium plan"
    expiry_text = "—"

    expiry = data.get("expiry_time") if data else None
    if expiry:
        tz = pytz.timezone("Asia/Kolkata")
        try:
            if expiry.tzinfo is None:
                expiry = tz.localize(expiry)
            else:
                expiry = expiry.astimezone(tz)
            now = datetime.datetime.now(tz)
            remaining = expiry - now
            expiry_text = expiry.strftime("%d %b %Y  •  %I:%M %p")
            if remaining.total_seconds() > 0:
                total_seconds = int(remaining.total_seconds())
                days, rem = divmod(total_seconds, 86400)
                hours, rem = divmod(rem, 3600)
                minutes = rem // 60
                status = "● ACTIVE"
                left = f"{days}d  {hours}h  {minutes}m"
            else:
                status = "● EXPIRED"
                left = "Expired"
        except Exception:
            status = "● ACTIVE"
            left = "Premium status available"

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
        [_btn("💎  Extend / Upgrade", callback_data="premium_info")],
        [_btn("⬅  Back", callback_data="premium_info"), _btn("🏠  Home", callback_data="ui_home")],
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
            return
    except Exception:
        pass
    await _edit(query, text, markup)


@Client.on_callback_query(filters.regex(r"^ui_home$"))
async def ui_home(_, query):
    await show_home(query)


@Client.on_callback_query(filters.regex(r"^ui_search$"))
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


@Client.on_callback_query(filters.regex(r"^ui_help$"))
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
            [_btn("🆘  Support", url=GRP_LNK or UPDATE_CHNL_LNK or "https://t.me/officialmymovies")],
        ),
    )


@Client.on_callback_query(filters.regex(r"^ui_h_search$"))
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


@Client.on_callback_query(filters.regex(r"^ui_account$"))
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


@Client.on_callback_query(filters.regex(r"^ui_request$"))
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
            [_btn("📨  Open Request Group", url=GRP_LNK or UPDATE_CHNL_LNK or "https://t.me")],
        ),
    )


@Client.on_callback_query(filters.regex(r"^ui_about$"))
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
            [_btn("👤  Owner", url=OWNER_LNK)],
            [_btn("📢  Updates", url=UPDATE_CHNL_LNK)],
        ),
    )


@Client.on_callback_query(filters.regex(r"^ui_settings$"))
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


@Client.on_callback_query(filters.regex(r"^ui_myplan$"))
async def ui_myplan(_, query):
    await show_myplan(query)


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
