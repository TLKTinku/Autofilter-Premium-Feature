from pyrogram import Client, filters
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from info import UPDATE_CHNL_LNK, GRP_LNK, PICS
from utils import temp
import random


def home_kb():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔎 Search Movies", callback_data="ui_search")],
        [
            InlineKeyboardButton("🔥 Trending", callback_data="topsearch"),
            InlineKeyboardButton("🆕 Latest", url=UPDATE_CHNL_LNK or "https://t.me"),
        ],
        [
            InlineKeyboardButton("👤 My Account", callback_data="ui_account"),
            InlineKeyboardButton("📨 Request", callback_data="ui_request"),
        ],
        [
            InlineKeyboardButton("⚙️ Settings", callback_data="ui_settings"),
            InlineKeyboardButton("❓ Help", callback_data="ui_help"),
        ],
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
        "       🎬 <b>𝐌𝐘 𝐌𝐎𝐕𝐈𝐄𝐒</b>\n"
        "   ᴍᴏᴠɪᴇs • sᴇʀɪᴇs • ᴀɴɪᴍᴇ • ᴋᴅʀᴀᴍᴀ\n"
        "╰━━━━━━━━━━━━━━━━━━━━╯\n\n"
        f"👋 Hey, {name}!\n\n"
        "🍿 Find your favourite movies,\n"
        "series and more in seconds.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n"
        "        ✦ <b>𝐌𝐄𝐍𝐔</b> ✦\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "⚡ Fast • Simple • Organized"
    )


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


@Client.on_callback_query(filters.regex(r"^ui_home$"))
async def ui_home(_, query):
    await show_home(query)


@Client.on_callback_query(filters.regex(r"^ui_search$"))
async def ui_search(_, query):
    await _edit(
        query,
        "╭────── 🔎 <b>𝐒𝐄𝐀𝐑𝐂𝐇</b> ──────╮\n\n"
        "Send me the name of a movie,\n"
        "series or anime.\n\n"
        "Example:\n"
        "<code>Avengers</code>\n"
        "<code>Interstellar</code>\n"
        "<code>Naruto</code>\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "💡 Tip: Try the original title.",
        InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Home", callback_data="ui_home")]]),
    )


@Client.on_callback_query(filters.regex(r"^ui_help$"))
async def ui_help(_, query):
    await _edit(
        query,
        "❓ <b>𝐇𝐄𝐋𝐏 𝐂𝐄𝐍𝐓𝐄𝐑</b>\n\n"
        "🔎 Search\n"
        "Search for any movie or series.\n\n"
        "🎞 Files\n"
        "Choose language and quality.\n\n"
        "📨 Request\n"
        "Can't find a movie? Send a request.\n\n"
        "⚙️ Settings\n"
        "Customize your preferences.",
        nav_kb(
            [InlineKeyboardButton("🔎 Search Guide", callback_data="ui_h_search")],
            [InlineKeyboardButton("📨 Request Guide", callback_data="ui_request")],
            [InlineKeyboardButton("🆘 Support", url=GRP_LNK or UPDATE_CHNL_LNK or "https://t.me/officialmymovies")],
        ),
    )


@Client.on_callback_query(filters.regex(r"^ui_h_search$"))
async def ui_h_search(_, query):
    await _edit(
        query,
        "🔎 <b>ꜱᴇᴀʀᴄʜ</b>\n\n"
        "Type the title in this chat or the group.\n"
        "Movie + year, or series + S01.",
        nav_kb(),
    )


@Client.on_callback_query(filters.regex(r"^ui_account$"))
async def ui_account(_, query):
    u = query.from_user
    await _edit(
        query,
        "╭──── 👤 <b>𝐌𝐘 𝐀𝐂𝐂𝐎𝐔𝐍𝐓</b> ────╮\n\n"
        f"👤 Name: {u.mention}\n"
        f"🆔 ID: <code>{u.id}</code>\n\n"
        "💎 Plan: Free\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━",
        nav_kb(
            [InlineKeyboardButton("💎 Premium", callback_data="premium_info")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="ui_settings")],
        ),
    )


@Client.on_callback_query(filters.regex(r"^ui_request$"))
async def ui_request(_, query):
    await _edit(
        query,
        "📨 <b>𝐑𝐄𝐐𝐔𝐄𝐒𝐓 𝐌𝐎𝐕𝐈𝐄</b>\n\n"
        "Can't find what you're looking for?\n\n"
        "Send:\n"
        "🎬 Movie / Series Name\n"
        "📅 Year\n"
        "🌐 Language\n\n"
        "Example:\n"
        "<code>Interstellar 2014</code>",
        nav_kb(),
    )


@Client.on_callback_query(filters.regex(r"^ui_settings$"))
async def ui_settings(_, query):
    await _edit(
        query,
        "⚙️ <b>𝐒𝐄𝐓𝐓𝐈𝐍𝐆𝐒</b>\n\n"
        "Customize your MY MOVIES experience.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "Group options: send /settings in the group.",
        nav_kb(
            [InlineKeyboardButton("💎 Premium", callback_data="premium_info")],
        ),
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
    else:
        try:
            await query.answer()
        except Exception:
            pass
