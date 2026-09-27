from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from info import UPDATE_CHNL_LNK
from utils import temp


def home_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔎 Search", callback_data="ui_search")],
        [
            InlineKeyboardButton("🔥 Trending", callback_data="topsearch"),
            InlineKeyboardButton("🆕 Latest", url=UPDATE_CHNL_LNK or "https://t.me"),
        ],
        [InlineKeyboardButton("👤 My Account", callback_data="ui_account")],
        [
            InlineKeyboardButton("📩 Request", callback_data="ui_request"),
            InlineKeyboardButton("❓ Help", callback_data="ui_help"),
        ],
        [
            InlineKeyboardButton("⭐ Premium", callback_data="premium_info"),
            InlineKeyboardButton("⚙️ Settings", callback_data="ui_settings"),
        ],
        [InlineKeyboardButton("➕ Add to Group", url=f"http://t.me/{temp.U_NAME}?startgroup=true")],
    ])


def nav_kb(*rows):
    out = list(rows)
    out.append([
        InlineKeyboardButton("⬅️ Back", callback_data="ui_home"),
        InlineKeyboardButton("🏠 Home", callback_data="ui_home"),
    ])
    return InlineKeyboardMarkup(out)


def home_text(name="there"):
    return (
        "╭━━━━━━━━━━━━━━━━━━━━╮\n"
        "        🎬 <b>MOVIE HUB</b>\n"
        "     Fast file search bot\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
        f"Hi {name}\n\n"
        "Movies • Series • Anime • KDrama\n"
        "Type a name to search."
    )


async def show_home(query):
    await query.message.edit_text(
        home_text(query.from_user.mention),
        reply_markup=home_kb(),
        disable_web_page_preview=True,
    )
    await query.answer()


@Client.on_callback_query(filters.regex(r"^ui_home$"))
async def ui_home(client, query):
    await show_home(query)


@Client.on_callback_query(filters.regex(r"^ui_search$"))
async def ui_search(client, query):
    await query.message.edit_text(
        "🔎 <b>Search</b>\n\n"
        "Send the title here.\n\n"
        "<code>Avatar</code>\n"
        "<code>Breaking Bad S01</code>\n"
        "<code>Joker 2019</code>",
        reply_markup=nav_kb(),
    )
    await query.answer()


@Client.on_callback_query(filters.regex(r"^ui_help$"))
async def ui_help(client, query):
    await query.message.edit_text(
        "❓ <b>Help</b>\n\nPick one:",
        reply_markup=nav_kb(
            [InlineKeyboardButton("🔎 How to search", callback_data="ui_h_search")],
            [InlineKeyboardButton("📥 How to download", callback_data="ui_h_dl")],
            [InlineKeyboardButton("📩 Request", callback_data="ui_request")],
            [InlineKeyboardButton("⭐ Premium", callback_data="premium_info")],
        ),
    )
    await query.answer()


@Client.on_callback_query(filters.regex(r"^ui_h_search$"))
async def ui_h_search(client, query):
    await query.message.edit_text(
        "🔎 <b>How to search</b>\n\n"
        "Send the name in this chat or in the group.\n"
        "Movie + year, or series + S01.",
        reply_markup=nav_kb(),
    )
    await query.answer()


@Client.on_callback_query(filters.regex(r"^ui_h_dl$"))
async def ui_h_dl(client, query):
    await query.message.edit_text(
        "📥 <b>How to download</b>\n\n"
        "Open the file Telegram sends.\n"
        "Save it or forward it to Saved Messages.",
        reply_markup=nav_kb(),
    )
    await query.answer()


@Client.on_callback_query(filters.regex(r"^ui_account$"))
async def ui_account(client, query):
    u = query.from_user
    await query.message.edit_text(
        "╭──── 👤 <b>My Account</b> ────╮\n\n"
        f"Name : {u.mention}\n"
        f"ID : <code>{u.id}</code>\n"
        "Plan : Free\n\n"
        "╰───────────────────────╯",
        reply_markup=nav_kb(
            [InlineKeyboardButton("⭐ Premium", callback_data="premium_info")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="ui_settings")],
        ),
    )
    await query.answer()


@Client.on_callback_query(filters.regex(r"^ui_request$"))
async def ui_request(client, query):
    await query.message.edit_text(
        "📩 <b>Request</b>\n\n"
        "Send the title in the group.\n\n"
        "<code>Interstellar 2014</code>",
        reply_markup=nav_kb(
            [InlineKeyboardButton("📢 Updates", url=UPDATE_CHNL_LNK or "https://t.me")],
        ),
    )
    await query.answer()


@Client.on_callback_query(filters.regex(r"^ui_settings$"))
async def ui_settings(client, query):
    await query.message.edit_text(
        "⚙️ <b>Settings</b>\n\n"
        "Group settings: use /settings in the group.\n"
        "Search still works by typing a name.",
        reply_markup=nav_kb(
            [InlineKeyboardButton("⭐ Premium", callback_data="premium_info")],
        ),
    )
    await query.answer()
