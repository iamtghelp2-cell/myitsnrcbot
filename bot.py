import sys
import os
from pathlib import Path

# Ensure root directory is in sys.path
BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

import asyncio
import datetime
import time
from datetime import timezone, timedelta

try:
    import uvloop
    asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
except Exception:
    pass

try:
    asyncio.get_event_loop()
except RuntimeError:
    asyncio.set_event_loop(asyncio.new_event_loop())

from pyrogram import Client, filters, enums
from pyrogram.types import (
    Message, CallbackQuery, BotCommand, InlineKeyboardMarkup, InlineKeyboardButton,
)
from pyrogram.errors import FloodWait

from config import (
    API_ID, API_HASH, BOT_TOKEN, LOG_CHANNEL, ADMINS,
    BOT_NAME, OWNER_USERNAME, CHANNEL_LINK, KEEP_ALIVE, DB_URI,
)
from auth import is_allowed
from database.db import db
from logger import LOGGER
from streamer import StreamSource, save_stream

logger = LOGGER(__name__)
IST = timezone(timedelta(hours=5, minutes=30))

USER_CACHE = set()
_DENY_THROTTLE = {}

BANNER = rf"""
  ___ _____ ____  _   _ ____   ____ ____   ___ _____
 |_ _|_   _/ ___|| \ | |  _ \ / ___| __ ) / _ \_   _|
  | |  | | \___ \|  \| | |_) | |   |  _ \| | | || |
  | |  | |  ___) | |\  |  _ <| |___| |_) | |_| || |
 |___| |_| |____/|_| \_|_| \_\____|____/ \___/ |_|
    {BOT_NAME} is starting...
"""

BOT_COMMANDS = [
    ("start", "Start the bot"),
    ("help", "Show help"),
    ("settings", "Open settings dashboard"),
    ("login", "Login account"),
    ("logout", "Logout account"),
    ("cancel", "Cancel current action"),
    ("myplan", "Check your plan"),
    ("premium", "Premium info"),
    ("setchat", "Set forward channel"),
    ("clearchat", "Stop forwarding to channel"),
    ("set_topics", "Set bulk topics link & keywords"),
    ("settopic", "Set default topic ID"),
    ("map_topic", "Map source topic to target topic"),
    ("show_topics", "View active topics list"),
    ("reset_topics", "Clear all topic mappings"),
    ("set_thumb", "Set thumbnail"),
    ("view_thumb", "View thumbnail"),
    ("del_thumb", "Delete thumbnail"),
    ("set_caption", "Set caption"),
    ("see_caption", "View caption"),
    ("del_caption", "Delete caption"),
    ("clean_ads", "Auto clean promo & ads links"),
    ("replace", "Quick replace word"),
    ("clear_replace", "Reset all replacements"),
    ("prefix", "Add header to caption"),
    ("suffix", "Add footer to caption"),
    ("set_del_word", "Add delete word"),
    ("rem_del_word", "Remove delete word"),
    ("set_repl_word", "Add replace word"),
    ("rem_repl_word", "Remove replace word"),
]


class Bot(Client):
    def __init__(self):
        super().__init__(
            name=BOT_NAME,
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
            plugins=dict(root="plugins"),
            workers=50,
            sleep_threshold=15,
            max_concurrent_transmissions=15,
            ipv6=False,
            in_memory=True,
        )
        self._keep_alive_started = False

    async def _log(self, text):
        if not LOG_CHANNEL:
            return
        try:
            await self.send_message(LOG_CHANNEL, text)
        except Exception as e:
            logger.error(f"Log channel send failed: {e}")

    async def start(self):
        print(BANNER)

        if KEEP_ALIVE and not self._keep_alive_started:
            try:
                from keep_alive import keep_alive
                keep_alive()
                self._keep_alive_started = True
                logger.info("Keep-alive server started.")
            except Exception as e:
                logger.warning(f"Keep-alive failed: {e}")

        while True:
            try:
                await super().start()
                break
            except FloodWait as e:
                wait = int(e.value) + 10
                logger.warning(f"FLOOD_WAIT during login. Sleeping {wait}s...")
                await asyncio.sleep(wait)
            except Exception as e:
                logger.error(f"Startup error: {e}")
                await asyncio.sleep(15)

        me = await self.get_me()
        try:
            await db.ensure_indexes()
            user_count = await db.total_users_count()
            logger.info(f"MongoDB connected: {user_count} users.")
        except Exception as e:
            logger.error(f"DB check failed: {e}")
            user_count = "?"

        now = datetime.datetime.now(IST)
        await self._log(
            f"<b><i>🤖 {BOT_NAME} started ♻️</i></b>\n\n"
            f"<b>Bot:</b> @{me.username}\n"
            f"<b>Users:</b> <code>{user_count}</code>\n"
            f"<b>Time:</b> <code>{now.strftime('%I:%M %p')} IST</code>"
        )
        await self.set_bot_commands_list()

    async def stop(self, *args):
        await self._log(f"<b><i>❌ {BOT_NAME} is going offline</i></b>")
        await asyncio.shield(super().stop())
        logger.info("Bot stopped cleanly")

    async def save_file(self, path, file_id=None, file_part=0, progress=None, progress_args=()):
        if isinstance(path, StreamSource):
            if file_id is not None:
                raise RuntimeError("A streamed upload cannot be resumed")
            return await save_stream(self, path)
        return await super().save_file(path, file_id, file_part, progress, progress_args)

    async def set_bot_commands_list(self):
        await self.set_bot_commands([BotCommand(c, d) for c, d in BOT_COMMANDS])


BotInstance = Bot()


def _deny_text(user_id):
    owner = OWNER_USERNAME or "Admin"
    ch = CHANNEL_LINK or "https://t.me"
    return (
        "<b>⛔ Access Denied / एक्सेस नहीं है</b>\n\n"
        "यह एक <b>प्राइवेट प्रीमियम बॉट</b> है। इसे इस्तेमाल करने के लिए प्रीमियम एक्सेस चाहिए।\n"
        "<i>This is a private premium bot. You need premium access to use it.</i>\n\n"
        f"👤 <b>Contact:</b> @{owner}\n"
        f"📢 <b>Channel:</b> {ch}\n"
        f"🆔 <b>Your ID:</b> <code>{user_id}</code> <i>(एक्सेस के लिए यह ID भेजें)</i>"
    )


def _deny_markup():
    owner = OWNER_USERNAME or "Admin"
    ch = CHANNEL_LINK or "https://t.me"
    return InlineKeyboardMarkup([
        [InlineKeyboardButton(f"👤 Contact @{owner}", url=f"https://t.me/{owner}")],
        [InlineKeyboardButton("📢 Join Channel", url=ch)],
    ])


@BotInstance.on_message(filters.private & filters.incoming, group=-2)
async def access_guard(bot: Client, message: Message):
    user = message.from_user
    if not user:
        message.stop_propagation()
    if await is_allowed(user.id):
        return
    now = time.time()
    if now - _DENY_THROTTLE.get(user.id, 0) > 2:
        _DENY_THROTTLE[user.id] = now
        try:
            await message.reply_text(
                _deny_text(user.id),
                parse_mode=enums.ParseMode.HTML,
                reply_markup=_deny_markup(),
                disable_web_page_preview=True,
            )
        except FloodWait as e:
            await asyncio.sleep(min(int(e.value), 5))
        except Exception as e:
            logger.warning(f"Could not send access-denied message to {user.id}: {e}")
    message.stop_propagation()


@BotInstance.on_callback_query(group=-2)
async def callback_guard(bot: Client, query: CallbackQuery):
    if await is_allowed(query.from_user.id):
        return
    try:
        await query.answer(f"⛔ Access denied. Contact @{OWNER_USERNAME}", show_alert=True)
    except Exception:
        pass
    query.stop_propagation()


@BotInstance.on_message(filters.private & filters.incoming, group=-1)
async def new_user_log(bot: Client, message: Message):
    user = message.from_user
    if not user or user.id in USER_CACHE:
        return
    try:
        existed = await db.is_user_exist(user.id)
        await db.add_user(user.id, user.first_name)
        if not existed:
            now = datetime.datetime.now(IST)
            await bot._log(
                f"<b>#NewUser 👤</b>\n<b>User:</b> {user.mention}\n"
                f"<b>ID:</b> <code>{user.id}</code>\n<b>Time:</b> {now.strftime('%I:%M %p')} IST"
            )
        USER_CACHE.add(user.id)
    except Exception as e:
        logger.error(f"new_user_log error: {e}")


@BotInstance.on_message(filters.command("cmd") & filters.user(ADMINS))
async def update_commands(bot: Client, message: Message):
    try:
        await bot.set_bot_commands_list()
        await message.reply_text("✅ Commands menu updated!")
    except Exception as e:
        await message.reply_text(f"❌ Error: {e}")


def _check_env():
    missing = [n for n, v in (("BOT_TOKEN", BOT_TOKEN), ("API_ID", API_ID), ("API_HASH", API_HASH),
                              ("DB_URI", DB_URI)) if not v]
    if missing:
        raise SystemExit(f"❌ Missing environment variables: {', '.join(missing)} - set them in host settings.")


if __name__ == "__main__":
    os.chdir(str(BASE_DIR))
    _check_env()
    BotInstance.run()
