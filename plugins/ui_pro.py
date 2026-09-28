import datetime
import random
import re

import pytz
from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto

from database.users_chats_db import db
from info import UPDATE_CHNL_LNK, GRP_LNK, PICS

# ─────────────────────────────────────────────────────────────────────────────
# MY MOVIES PRO — one shared design language for every screen.
#   Header : ╭━━━╮ 🎬 TITLE ╰━━━╯
#   Divider: ━━━━━━━━━━━━━━━━━━━━━━
#   Nav    : [ ⬅️ Back ] [ 🏠 Home ]
# ─────────────────────────────────────────────────────────────────────────────
LINE = "━━━━━━━━━━━━━━━━━━━━━━"

# Premium plans shown on the Premium page (same plans as the payment screens).
PLANS = (
    ("7 DAYS", "₹20"),
    ("15 DAYS", "₹40"),
    ("30 DAYS", "₹65"),
    ("45 DAYS", "₹90"),
    ("60 DAYS", "₹110"),
)


def header(title):
    return (
        "╭━━━━━━━━━━━━━━━━━━━━━━╮\n"
        f"        <b>{title}</b>\n"
        "╰━━━━━━━━━━━━━━━━━━━━━━╯"
    )


def plan_label(raw):
    """'7day' -> '7 Days', '1month' -> '1 Month' (for plan buttons)."""
    m = re.match(r"^\s*(\d+)\s*([a-zA-Z]+)", str(raw))
    if not m:
        return str(raw)
    n = int(m.group(1))
    unit = m.group(2).lower()
    for prefix, word in (("day", "Day"), ("month", "Month"), ("year", "Year"), ("hour", "Hour"), ("min", "Min")):
        if unit.startswith(prefix):
            return f"{n} {word}{'s' if n != 1 else ''}"
    return str(raw)


def _support_url():
    return GRP_LNK or UPDATE_CHNL_LNK or "https://t.me/officialmymovies"


# ── Keyboards ────────────────────────────────────────────────────────────────
def home_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔎 Search Movies", callback_data="ui_search")],
        [
            InlineKeyboardButton("🎬 Trending", callback_data="topsearch"),
            InlineKeyboardButton("💎 Premium", callback_data="premium_info"),
        ],
        [
            InlineKeyboardButton("👤 My Account", callback_data="ui_account"),
            InlineKeyboardButton("⚙️ Settings", callback_data="ui_settings"),
        ],
        [
            InlineKeyboardButton("❓ Help", callback_data="ui_help"),
            InlineKeyboardButton("ℹ️ About", callback_data="ui_about"),
        ],
    ])


def nav_kb(*rows, back=None):
    """Bottom navigation. `back` is the callback of the parent screen (if any)."""
    out = list(rows)
    if back:
        out.append([
            InlineKeyboardButton("⬅️ Back", callback_data=back),
            InlineKeyboardButton("🏠 Home", callback_data="ui_home"),
        ])
    else:
        out.append([InlineKeyboardButton("🏠 Home", callback_data="ui_home")])
    return InlineKeyboardMarkup(out)


# ── Texts ────────────────────────────────────────────────────────────────────
def home_text(name="there"):
    return (
        f"{header('🎬 MY MOVIES')}\n"
        "          YOUR MOVIE LIBRARY\n\n"
        f"👋 Welcome, {name}\n\n"
        "🔎 Search movies & series\n"
        "📨 Request titles you can't find\n"
        "💎 Unlock Premium features\n\n"
        f"{LINE}"
    )


def premium_overview_text():
    plan_lines = "\n".join(f"💎 <b>{days}</b>  •  {price}" for days, price in PLANS)
    return (
        f"{header('💎 PREMIUM')}\n"
        "       ✦ <b>MY MOVIES PRO</b> ✦\n\n"
        "🚫 No verification needed\n"
        "📂 Direct files, no extra links\n"
        "⚡ High-speed download & streaming\n"
        "✨ Ad-free experience\n"
        "📨 Fast request support\n\n"
        f"{LINE}\n\n"
        f"{plan_lines}\n\n"
        f"{LINE}\n"
        "Tap <b>Activate</b> to choose a payment method.\n"
        "Check your plan anytime: /myplan"
    )


def premium_overview_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("💎 Activate Premium", callback_data="buy_info")],
        [
            InlineKeyboardButton("🎁 Refer & Earn", callback_data="reffff"),
            InlineKeyboardButton("🆓 Free Trial", callback_data="give_trial"),
        ],
        [InlineKeyboardButton("🏠 Home", callback_data="ui_home")],
    ])


async def get_plan_info(user_id):
    """Plan/status details for a user. Never raises."""
    info = {
        "plan": "Free",
        "status": "● Free",
        "valid_until": None,
        "valid_label": "Valid Until",
        "left": None,
    }
    try:
        data = await db.get_user(user_id)
        expiry = data.get("expiry_time") if data else None
        if not expiry:
            return info
        ist = pytz.timezone("Asia/Kolkata")
        expiry_ist = expiry.astimezone(ist)
        remaining = expiry_ist - datetime.datetime.now(ist)
        info["valid_until"] = expiry_ist.strftime("%d %b %Y  •  %I:%M %p")
        total = int(remaining.total_seconds())
        if total > 0:
            days, rem = divmod(total, 86400)
            hours, rem = divmod(rem, 3600)
            info["plan"] = "Premium"
            info["status"] = "● Active"
            info["left"] = f"{days}d  {hours}h  {rem // 60}m"
        else:
            info["status"] = "● Expired"
            info["valid_label"] = "Expired On"
    except Exception:
        pass
    return info


def account_text(mention, user_id, info):
    lines = [
        header("👤 MY ACCOUNT"),
        "",
        f"👤 {mention}",
        f"🆔 <code>{user_id}</code>",
        "",
        "💎 <b>PLAN</b>",
        info["plan"],
        "",
    ]
    if info.get("valid_until"):
        lines += [f"📅 <b>{info['valid_label']}</b>", info["valid_until"], ""]
    if info.get("left"):
        lines += ["⏳ <b>Time Left</b>", info["left"], ""]
    lines += ["📊 <b>Status</b>", info["status"], "", LINE]
    return "\n".join(lines)


# ── Message editing helper ───────────────────────────────────────────────────
async def _edit(query, text, markup):
    msg = query.message
    pic = None
    try:
        if PICS:
            pic = random.choice(PICS)
    except Exception:
        pic = None
    try:
        if pic:
            await msg.edit_media(InputMediaPhoto(pic, caption=text), reply_markup=markup)
        elif msg.photo or msg.video or msg.animation or msg.document:
            await msg.edit_caption(caption=text, reply_markup=markup)
        else:
            await msg.edit_text(text, reply_markup=markup, disable_web_page_preview=True)
    except Exception:
        try:
            if pic:
                await msg.reply_photo(pic, caption=text, reply_markup=markup)
            else:
                await msg.reply_text(text, reply_markup=markup, disable_web_page_preview=True)
        except Exception:
            pass
    try:
        await query.answer()
    except Exception:
        pass


async def show_home(query):
    await _edit(query, home_text(query.from_user.mention), home_kb())


# ── Screens ──────────────────────────────────────────────────────────────────
@Client.on_callback_query(filters.regex(r"^ui_home$"))
async def ui_home(_, query):
    await show_home(query)


@Client.on_callback_query(filters.regex(r"^ui_search$"))
async def ui_search(_, query):
    await _edit(
        query,
        f"{header('🔎 SEARCH')}\n\n"
        "Send me the name of a movie,\n"
        "series or anime.\n\n"
        "Examples:\n"
        "<code>Interstellar 2014</code>\n"
        "<code>Naruto</code>\n"
        "<code>Money Heist S01</code>\n\n"
        f"{LINE}\n"
        "💡 Tip: try the original title.",
        nav_kb(),
    )


@Client.on_callback_query(filters.regex(r"^ui_help$"))
async def ui_help(_, query):
    await _edit(
        query,
        f"{header('❓ HELP')}\n\n"
        "🎬 <b>HOW TO USE</b>\n\n"
        "1️⃣ Send a movie name\n"
        "2️⃣ Select your movie\n"
        "3️⃣ Choose quality\n"
        "4️⃣ Select your file\n"
        "5️⃣ Enjoy 🎬\n\n"
        f"{LINE}",
        nav_kb(
            [
                InlineKeyboardButton("🔎 Search Guide", callback_data="ui_h_search"),
                InlineKeyboardButton("📨 Request Guide", callback_data="ui_request"),
            ],
            [InlineKeyboardButton("💎 Premium Help", callback_data="premium_info")],
            [InlineKeyboardButton("🛠️ Support", url=_support_url())],
        ),
    )


@Client.on_callback_query(filters.regex(r"^ui_h_search$"))
async def ui_h_search(_, query):
    await _edit(
        query,
        f"{header('🔎 SEARCH GUIDE')}\n\n"
        "🎬 Movies\n"
        "Movie name + year\n"
        "<code>Joker 2019</code>\n\n"
        "📺 Series\n"
        "Series name + season\n"
        "<code>Money Heist S01</code>\n\n"
        "🌐 Language\n"
        "Add the language to the name\n"
        "<code>Naruto Hindi</code>\n\n"
        f"{LINE}\n"
        "Use the Quality, Language and Season\n"
        "buttons to narrow down files.",
        nav_kb(back="ui_help"),
    )


@Client.on_callback_query(filters.regex(r"^ui_account$"))
async def ui_account(_, query):
    u = query.from_user
    info = await get_plan_info(u.id)
    upgrade_label = "💎 Extend Plan" if info["plan"] == "Premium" else "💎 Upgrade Plan"
    await _edit(
        query,
        account_text(u.mention, u.id, info),
        nav_kb([InlineKeyboardButton(upgrade_label, callback_data="premium_info")]),
    )


@Client.on_callback_query(filters.regex(r"^ui_request$"))
async def ui_request(_, query):
    await _edit(
        query,
        f"{header('📨 REQUEST')}\n\n"
        "Can't find what you're looking for?\n\n"
        "Send these details:\n"
        "🎬 Movie / Series name\n"
        "📅 Year\n"
        "🌐 Language\n\n"
        "Example:\n"
        "<code>Interstellar 2014</code>\n\n"
        f"{LINE}\n"
        "In a group, use <code>/request name</code>.",
        nav_kb(back="ui_help"),
    )


@Client.on_callback_query(filters.regex(r"^ui_settings$"))
async def ui_settings(_, query):
    await _edit(
        query,
        f"{header('⚙️ SETTINGS')}\n\n"
        "Customize your MY MOVIES experience.\n\n"
        "👥 <b>Group settings</b>\n"
        "Send /settings inside your group.\n"
        "Only group admins can change them.\n\n"
        "💎 <b>Plan</b>\n"
        "See your plan in My Account.\n\n"
        f"{LINE}",
        nav_kb(
            [
                InlineKeyboardButton("👤 My Account", callback_data="ui_account"),
                InlineKeyboardButton("💎 Premium", callback_data="premium_info"),
            ],
        ),
    )


@Client.on_callback_query(filters.regex(r"^ui_about$"))
async def ui_about(_, query):
    rows = []
    links = []
    if UPDATE_CHNL_LNK:
        links.append(InlineKeyboardButton("🆕 Latest Updates", url=UPDATE_CHNL_LNK))
    if GRP_LNK:
        links.append(InlineKeyboardButton("💬 Community", url=GRP_LNK))
    if links:
        rows.append(links)
    await _edit(
        query,
        f"{header('🎬 MY MOVIES')}\n\n"
        "Your personal movie\n"
        "search & file provider.\n\n"
        f"{LINE}\n\n"
        "⚡ Fast Search\n"
        "🎬 Movies & Series\n"
        "💎 Premium\n"
        "🔎 Smart Filtering\n\n"
        f"{LINE}\n"
        "Powered by MY MOVIES",
        nav_kb(*rows),
    )


async def dispatch_ui(query):
    data = query.data or ""
    if data == "ui_home":
        await show_home(query)
    elif data == "ui_search":
        await ui_search(None, query)
    elif data == "ui_help":
        await ui_help(None, query)
    elif data == "ui_h_search":
        await ui_h_search(None, query)
    elif data == "ui_account":
        await ui_account(None, query)
    elif data == "ui_request":
        await ui_request(None, query)
    elif data == "ui_settings":
        await ui_settings(None, query)
    elif data == "ui_about":
        await ui_about(None, query)
    else:
        try:
            await query.answer()
        except Exception:
            pass
