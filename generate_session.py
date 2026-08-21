"""
Run this ONCE on your own computer (not on Render) to generate a session string
for your personal Telegram account. This lets your bot's "userbot" join channels
via invite link that you can't make the bot itself an admin of.

How to use:
1. pip install pyrogram tgcrypto
2. Fill in your API_ID and API_HASH below (same ones from my.telegram.org that you use for the bot)
3. Run: python generate_session.py
4. Enter your phone number, the OTP code Telegram sends you, and your 2FA password if you have one
5. Copy the printed session string and put it in Render as the USER_SESSION variable

⚠️ Keep this session string PRIVATE. Anyone who has it can fully control your Telegram account.
"""

from pyrogram import Client

API_ID = 12345          # <-- put your API_ID here
API_HASH = "your_api_hash_here"   # <-- put your API_HASH here

with Client("my_userbot_session", api_id=API_ID, api_hash=API_HASH, in_memory=True) as app:
    session_string = app.export_session_string()
    print("\n\n✅ Your session string (copy everything below this line):\n")
    print(session_string)
    print("\n⚠️ Keep this private! Put it in Render as USER_SESSION.\n")
