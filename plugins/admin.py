from pyrogram import Client, filters
from pyrogram.types import Message

from auth import invalidate, is_sudo
from config import ADMINS
from database.db import db


@Client.on_message(filters.command("ban") & filters.user(ADMINS))
async def ban(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text("**Usage:** `/ban user_id`")
    try:
        user_id = int(message.command[1])
        if is_sudo(user_id):
            return await message.reply_text("⚠️ Sudo/Admin यूज़र को ban नहीं किया जा सकता।")
        await db.ban_user(user_id)
        invalidate(user_id)
        await message.reply_text(f"**User {user_id} banned 🚫**")
    except Exception as e:
        await message.reply_text(f"Error: `{e}`")


@Client.on_message(filters.command("unban") & filters.user(ADMINS))
async def unban(client: Client, message: Message):
    if len(message.command) < 2:
        return await message.reply_text("**Usage:** `/unban user_id`")
    try:
        user_id = int(message.command[1])
        await db.unban_user(user_id)
        invalidate(user_id)
        await message.reply_text(f"**User {user_id} unbanned ✅**")
    except Exception as e:
        await message.reply_text(f"Error: `{e}`")


@Client.on_message(filters.command("set_dump") & filters.user(ADMINS))
async def set_dump(client: Client, message: Message):
    if len(message.command) < 3:
        return await message.reply_text("**Usage:** `/set_dump user_id chat_id`")
    try:
        await db.set_dump_chat(int(message.command[1]), int(message.command[2]))
        await message.reply_text(f"**Dump chat set for user {message.command[1]}.**")
    except Exception as e:
        await message.reply_text(f"Error: `{e}`")
