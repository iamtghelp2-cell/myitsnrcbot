"""Owner controls who can use the bot (paid users) + /myplan and /premium."""

from datetime import timezone, timedelta

from pyrogram import Client, filters, enums
from pyrogram.types import Message

from auth import invalidate, is_owner, is_sudo
from config import ADMINS, BOT_NAME, CHANNEL_LINK, OWNER_USERNAME
from database.db import db

IST = timezone(timedelta(hours=5, minutes=30))
HTML = enums.ParseMode.HTML


def _fmt(until):
    if not until:
        return "♾️ Lifetime (कभी खत्म नहीं)"
    dt = until.replace(tzinfo=timezone.utc).astimezone(IST)
    return dt.strftime("%d %b %Y, %I:%M %p IST")


def _is_uid(text):
    return bool(text) and text.isdigit() and int(text) > 0


@Client.on_message(filters.command(["adduser", "add_premium"]) & filters.user(ADMINS) & filters.private)
async def add_user_cmd(client: Client, message: Message):
    args = message.command[1:]
    if not args or not _is_uid(args[0]):
        return await message.reply_text(
            "<b>Usage:</b>\n"
            "<code>/add_premium user_id days</code> → उतने दिन के लिए (जैसे 30)\n"
            "<code>/add_premium user_id</code> → हमेशा के लिए\n"
            "<i>(/adduser भी वही काम करता है। पहले से एक्टिव प्लान में दिन जुड़ जाते हैं।)</i>",
            parse_mode=HTML,
        )
    user_id = int(args[0])
    days = None
    if len(args) > 1:
        if not args[1].isdigit() or int(args[1]) <= 0:
            return await message.reply_text("❌ दिन एक सही संख्या होनी चाहिए (जैसे 30)।")
        days = int(args[1])

    until = await db.grant_access(user_id, days)
    invalidate(user_id)
    await message.reply_text(
        f"✅ <b>एक्सेस दे दिया गया</b>\n\n<b>User:</b> <code>{user_id}</code>\n<b>Valid:</b> {_fmt(until)}",
        parse_mode=HTML,
    )
    try:
        await client.send_message(
            user_id,
            f"🎉 <b>आपको {BOT_NAME} का एक्सेस मिल गया है!</b>\n\n<b>Valid:</b> {_fmt(until)}\n\n/start भेजकर शुरू करें।",
            parse_mode=HTML,
        )
    except Exception:
        await message.reply_text("ℹ️ यूज़र को मैसेज नहीं जा सका (उसने अभी बॉट स्टार्ट नहीं किया होगा), पर एक्सेस मिल गया है।")


@Client.on_message(filters.command(["removeuser", "remove_premium"]) & filters.user(ADMINS) & filters.private)
async def remove_user_cmd(client: Client, message: Message):
    args = message.command[1:]
    if not args or not _is_uid(args[0]):
        return await message.reply_text("<b>Usage:</b> <code>/remove_premium user_id</code>", parse_mode=HTML)
    user_id = int(args[0])
    if is_sudo(user_id):
        return await message.reply_text("⚠️ यह ID कोड में fixed (sudo) है, इसे बॉट से नहीं हटाया जा सकता।")
    await db.revoke_access(user_id)
    invalidate(user_id)
    await message.reply_text(f"🗑️ <code>{user_id}</code> का एक्सेस हटा दिया गया।", parse_mode=HTML)
    try:
        await client.send_message(user_id, "⛔ आपका एक्सेस समाप्त कर दिया गया है।")
    except Exception:
        pass


@Client.on_message(filters.command(["allowed", "premium_users"]) & filters.user(ADMINS) & filters.private)
async def allowed_cmd(client: Client, message: Message):
    users = await db.list_access()
    if not users:
        return await message.reply_text("ℹ️ अभी कोई पेड/एक्स्ट्रा यूज़र नहीं है।")
    now = db.utcnow()
    lines = ["<b>👥 एक्स्ट्रा यूज़र्स:</b>\n"]
    for u in users:
        until = u.get("access_until")
        status = "❌ expired" if until and until < now else "✅"
        lines.append(f"{status} <code>{u['id']}</code> — {u.get('name') or '-'} — {_fmt(until)}")
    await message.reply_text("\n".join(lines)[:4000], parse_mode=HTML)


@Client.on_message(filters.command("myplan") & filters.private)
async def myplan_cmd(client: Client, message: Message):
    uid = message.from_user.id
    if is_owner(uid):
        plan = "👑 Owner (Lifetime)"
    elif is_sudo(uid):
        plan = "✅ Sudo user (Lifetime)"
    else:
        ok, until = await db.get_access(uid)
        plan = f"💎 Premium user\n<b>Valid till:</b> {_fmt(until)}" if ok else "❌ कोई एक्टिव प्लान नहीं"
    await message.reply_text(f"⭐ <b>आपका प्लान:</b>\n{plan}", parse_mode=HTML)


@Client.on_message(filters.command("premium") & filters.private)
async def premium_cmd(client: Client, message: Message):
    await message.reply_text(
        f"💎 <b>{BOT_NAME} Access</b>\n\n"
        f"यह प्राइवेट बॉट है। एक्सेस के लिए @{OWNER_USERNAME} से संपर्क करें।\n"
        f"📢 Updates: {CHANNEL_LINK}",
        parse_mode=HTML,
        disable_web_page_preview=True,
    )
