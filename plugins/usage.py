import os
import time
from pyrogram import Client, filters
from info import ADMINS, MULTIPLE_DB, COLLECTION_NAME
from database.ia_filterdb import Media, Media2
from database.users_chats_db import db

_STARTED = time.time()


def _read(path):
    try:
        with open(path, "r") as f:
            return f.read()
    except Exception:
        return ""


def _kb(n):
    n = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _uptime(sec):
    sec = int(sec)
    d, sec = divmod(sec, 86400)
    h, sec = divmod(sec, 3600)
    m, s = divmod(sec, 60)
    if d:
        return f"{d}d {h}h {m}m"
    if h:
        return f"{h}h {m}m"
    return f"{m}m {s}s"


def _host():
    rss = 0
    for line in _read("/proc/self/status").splitlines():
        if line.startswith("VmRSS:"):
            rss = int(line.split()[1]) * 1024
            break
    mem_total = mem_avail = 0
    for line in _read("/proc/meminfo").splitlines():
        if line.startswith("MemTotal:"):
            mem_total = int(line.split()[1]) * 1024
        elif line.startswith("MemAvailable:"):
            mem_avail = int(line.split()[1]) * 1024
    mem_used = max(mem_total - mem_avail, 0)
    load = " ".join(_read("/proc/loadavg").split()[:3]) or "-"
    try:
        st = os.statvfs("/")
        d_total = st.f_frsize * st.f_blocks
        d_free = st.f_frsize * st.f_bavail
        d_used = d_total - d_free
    except Exception:
        d_total = d_used = 0
    return rss, mem_used, mem_total, d_used, d_total, load


async def _coll(mongo, name):
    try:
        s = await mongo.command("collStats", name)
        return int(s.get("count", 0) or 0), int(s.get("size", 0) or 0)
    except Exception:
        return 0, 0


@Client.on_message(filters.command(["usage", "ram", "server", "host"]) & filters.user(ADMINS))
async def usage_cmd(client, message):
    msg = await message.reply_text("⏳ Checking host + MongoDB...")
    rss, mem_used, mem_total, d_used, d_total, load = _host()
    mongo = Media.collection.database
    files, files_sz = await _coll(mongo, COLLECTION_NAME)
    files2 = files2_sz = 0
    if MULTIPLE_DB:
        try:
            files2, files2_sz = await _coll(Media2.collection.database, COLLECTION_NAME)
        except Exception:
            pass
    users, users_sz = await _coll(mongo, "users")
    groups, groups_sz = await _coll(mongo, "groups")
    hashes, hashes_sz = await _coll(mongo, "userbot_seen_hashes")
    misc, misc_sz = await _coll(mongo, "misc")
    try:
        dbstats = await mongo.command("dbStats")
        data_sz = int(dbstats.get("dataSize", 0) or 0)
        storage_sz = int(dbstats.get("storageSize", 0) or 0)
    except Exception:
        data_sz = files_sz + users_sz + groups_sz + hashes_sz + misc_sz
        storage_sz = data_sz

    text = (
        f"<b>Bot usage</b>\n\n"
        f"<b>Host</b>\n"
        f"RAM (bot): <code>{_kb(rss)}</code>\n"
        f"RAM (server): <code>{_kb(mem_used)}</code> / <code>{_kb(mem_total)}</code>\n"
        f"Disk: <code>{_kb(d_used)}</code> / <code>{_kb(d_total)}</code>\n"
        f"CPU load: <code>{load}</code>\n"
        f"Uptime: <code>{_uptime(time.time() - _STARTED)}</code>\n\n"
        f"<b>MongoDB</b>\n"
        f"Files: <code>{files:,}</code> ({_kb(files_sz)})\n"
    )
    if MULTIPLE_DB:
        text += f"Files DB2: <code>{files2:,}</code> ({_kb(files2_sz)})\n"
    text += (
        f"Users: <code>{users:,}</code>\n"
        f"Groups: <code>{groups:,}</code>\n"
        f"Dup hashes: <code>{hashes:,}</code> ({_kb(hashes_sz)})\n"
        f"Misc: <code>{misc:,}</code>\n"
        f"DB data: <code>{_kb(data_sz)}</code>\n"
        f"DB storage: <code>{_kb(storage_sz)}</code>"
    )
    await msg.edit_text(text)
