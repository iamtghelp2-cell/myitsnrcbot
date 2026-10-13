# ==============================================================================
# Itsnrcbot - main features: link saver / forwarder, topics, caption, thumbnail,
# replace / delete words, prefix / suffix, ad cleaner.
# ==============================================================================

import re
import time
import html
import asyncio
import logging

from pyrogram import Client, filters, enums
from pyrogram.errors import (
    FloodWait, AuthKeyUnregistered, SessionRevoked, SessionExpired, UserDeactivated,
    UserDeactivatedBan, AuthKeyDuplicated,
)
from pyrogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from config import (
    API_ID, API_HASH, STRING_SESSION, COPY_DELAY, MAX_BATCH, BOT_NAME, CHANNEL_LINK,
    RAM_FILE_LIMIT_MB, STREAM_WORKERS, STREAM_SEGMENT_MB, UPLOAD_PARALLEL, USER_SESSION_COPY,
)
from database.db import db
from helpers import (
    parse_tg_link, split_range, get_media_type, file_info, chunks, build_caption,
    build_text, renamed_name, default_filename, resolve_target_topic, is_protected,
)
try:
    from plugins.session import login_state_filter
except Exception:
    login_state_filter = filters.create(lambda _, __, ___: False)
from streamer import StreamSource, StreamAborted, MIB

SESSION_DEAD_ERRORS = (AuthKeyUnregistered, SessionRevoked, SessionExpired, UserDeactivated,
                       UserDeactivatedBan, AuthKeyDuplicated)
STREAMABLE = ("video", "animation", "document", "audio")  # types that can be big

logger = logging.getLogger(__name__)
HTML = enums.ParseMode.HTML
PLAIN = enums.ParseMode.DISABLED

# errors that mean "the bot itself can't copy from this chat" -> stop trying copy_message
COPY_BLOCKING_ERRORS = {
    "CHAT_FORWARDS_RESTRICTED", "CHANNEL_PRIVATE", "CHANNEL_INVALID", "PEER_ID_INVALID",
    "CHAT_ID_INVALID", "USER_NOT_PARTICIPANT", "CHAT_ADMIN_REQUIRED", "MESSAGE_ID_INVALID",
}


class State:
    IS_BUSY = {}


# ------------------------------------------------------------------------------
# Per-user settings (saved in MongoDB, cached in memory)
# ------------------------------------------------------------------------------
CFG = {}
_PAIR_KEYS = ("replace", "topic_kw", "topic_direct")


def _new_cfg():
    return {
        "clean_ads": False, "prefix": "", "suffix": "", "caption": "", "thumb": None,
        "default_topic": None, "delete": [], "replace": {}, "topic_kw": {}, "topic_direct": {},
    }


async def get_cfg(uid: int) -> dict:
    cfg = CFG.get(uid)
    if cfg is not None:
        return cfg
    cfg = _new_cfg()
    try:
        raw = await db.get_cfg(uid)
    except Exception as e:
        logger.warning(f"Could not load settings for {uid}: {e}")
        raw = {}
    cfg["clean_ads"] = bool(raw.get("clean_ads"))
    for key in ("prefix", "suffix", "caption"):
        cfg[key] = raw.get(key) or ""
    cfg["thumb"] = raw.get("thumb")
    cfg["default_topic"] = raw.get("default_topic")
    cfg["delete"] = list(raw.get("delete") or [])
    for key in _PAIR_KEYS:
        cfg[key] = {a: b for a, b in (raw.get(key) or [])}
    CFG[uid] = cfg
    return cfg


async def save_cfg(uid: int):
    cfg = CFG.get(uid)
    if cfg is None:
        return
    raw = dict(cfg)
    for key in _PAIR_KEYS:  # Mongo keys can't hold dots -> store as [[k, v], ...]
        raw[key] = [[a, b] for a, b in cfg[key].items()]
    try:
        await db.set_cfg(uid, raw)
    except Exception as e:
        logger.error(f"Could not save settings for {uid}: {e}")


def _arg(message: Message):
    parts = message.text.split(None, 1)
    return parts[1].strip() if len(parts) > 1 else ""


# ------------------------------------------------------------------------------
# Basic commands
# ------------------------------------------------------------------------------
@Client.on_message(filters.command("start") & filters.private)
async def start_handler(bot: Client, message: Message):
    await message.reply_text(
        f"👋 <b>नमस्ते {message.from_user.mention}!</b>\n\n"
        f"⚡ <b>{BOT_NAME} — फास्ट सेव/फॉरवर्ड बॉट सक्रिय है।</b>\n\n"
        "• टारगेट चैनल/ग्रुप सेट करें: <code>/setchat -100xxxxxxxxxx</code>\n"
        "• बॉट चैट में ही पाने के लिए: <code>/clearchat</code>\n"
        "• प्राइवेट चैनल के लिए: <code>/login</code>\n"
        "• सारी कमांड्स: <code>/help</code> | सेटिंग्स: <code>/settings</code>\n"
        "• चालू टास्क रोकें: <code>/cancel</code>\n\n"
        "📌 <b>उपयोग:</b> पब्लिक/प्राइवेट लिंक भेजें\n"
        "<code>https://t.me/c/123/10</code>  या रेंज  <code>https://t.me/c/123/10-20</code>\n\n"
        f"📢 {CHANNEL_LINK}",
        parse_mode=HTML,
        disable_web_page_preview=True,
    )


@Client.on_message(filters.command("help") & filters.private)
async def help_handler(bot: Client, message: Message):
    await message.reply_text(
        "📖 <b>सभी कमांड्स:</b>\n\n"
        "<b>बेसिक</b>\n"
        "/start /help /settings /myplan /premium\n"
        "/login - अकाउंट लॉगिन | /logout - लॉगआउट\n"
        "/cancel - चालू टास्क/लॉगिन रोकें\n\n"
        "<b>डेस्टिनेशन व टॉपिक</b>\n"
        "<code>/setchat -100xxxxxxxxxx</code> - फॉरवर्ड चैनल | <code>/clearchat</code> - बंद करें\n"
        "<code>/set_topics</code> - कीवर्ड→टॉपिक लिस्ट\n"
        "<code>/settopic &lt;id&gt;</code> - डिफ़ॉल्ट टॉपिक\n"
        "<code>/map_topic &lt;src&gt; &lt;dest&gt;</code> - टॉपिक मैप\n"
        "/show_topics - लिस्ट | /reset_topics - सब हटाएं\n\n"
        "<b>थंबनेल व कैप्शन</b>\n"
        "/set_thumb (फोटो पर रिप्लाई) | /view_thumb | /del_thumb\n"
        "<code>/set_caption text</code> - कस्टम कैप्शन\n"
        "  में <code>{filename}</code> <code>{size}</code> <code>{caption}</code> चलते हैं\n"
        "/see_caption | /del_caption\n"
        "<code>/prefix text</code> - शुरू में जोड़ें | <code>/suffix text</code> - अंत में\n"
        "<code>/clean_ads on|off</code> - लिंक/@username हटाएं\n\n"
        "<b>शब्द बदलना / हटाना</b>\n"
        "<code>/replace पुराना:नया</code> या <code>पुराना-&gt;नया</code>\n"
        "<code>/set_repl_word पुराना नया</code> | <code>/rem_repl_word पुराना</code> | /clear_replace\n"
        "<code>/set_del_word शब्द1, शब्द2</code> | <code>/rem_del_word शब्द</code>\n\n"
        "<i>लिंक में : हो तो -&gt; इस्तेमाल करें, जैसे</i>\n"
        "<code>/replace https://old.com-&gt;https://new.com</code>",
        parse_mode=HTML,
    )


@Client.on_message(filters.command("settings") & filters.private)
async def settings_handler(bot: Client, message: Message):
    uid = message.from_user.id
    cfg = await get_cfg(uid)
    dump = await db.get_dump_chat(uid)
    on = lambda v: "✅ चालू" if v else "❌ बंद"
    has = lambda v: "✅ सेट है" if v else "❌ नहीं"
    await message.reply_text(
        "⚙️ <b>सेटिंग्स डैशबोर्ड</b>\n\n"
        f"• <b>टारगेट चैट:</b> <code>{dump or 'पर्सनल बॉट चैट'}</code>\n"
        f"• <b>डिफ़ॉल्ट टॉपिक:</b> <code>{cfg['default_topic'] or 'कोई नहीं'}</code>\n"
        f"• <b>टॉपिक कीवर्ड:</b> <code>{len(cfg['topic_kw'])}</code> | <b>मैपिंग:</b> <code>{len(cfg['topic_direct'])}</code>\n"
        f"• <b>Ad क्लीनर:</b> {on(cfg['clean_ads'])}\n"
        f"• <b>थंबनेल:</b> {has(cfg['thumb'])}\n"
        f"• <b>कैप्शन:</b> {has(cfg['caption'])}\n"
        f"• <b>प्रीफिक्स:</b> <code>{cfg['prefix'] or 'कोई नहीं'}</code>\n"
        f"• <b>सफ़िक्स:</b> <code>{cfg['suffix'] or 'कोई नहीं'}</code>\n"
        f"• <b>Replace नियम:</b> <code>{len(cfg['replace'])}</code> | <b>Delete शब्द:</b> <code>{len(cfg['delete'])}</code>",
        parse_mode=HTML,
    )


# ------------------------------------------------------------------------------
# Destination chat + topics
# ------------------------------------------------------------------------------
@Client.on_message(filters.command("setchat") & filters.private)
async def set_dump_chat(bot: Client, message: Message):
    uid = message.from_user.id
    args = message.text.split()
    if len(args) < 2:
        return await message.reply_text(
            "उपयोग:\n• चैनल सेट करें: <code>/setchat -100xxxxxxxxxx</code>\n• हटाएं: <code>/setchat clear</code>",
            parse_mode=HTML,
        )
    if args[1].lower() == "clear":
        await db.set_dump_chat(uid, None)
        cfg = await get_cfg(uid)
        cfg["default_topic"] = None
        await save_cfg(uid)
        return await message.reply_text("✅ टारगेट चैनल हटा दिया गया। फाइलें अब इसी चैट में आएँगी।")
    try:
        dest = int(args[1])
        chat = await bot.get_chat(dest)
        await db.set_dump_chat(uid, dest)
        await message.reply_text(
            f"✅ <b>टारगेट सेट हुआ:</b> {chat.title} [<code>{dest}</code>]", parse_mode=HTML
        )
    except ValueError:
        await message.reply_text("❌ चैट ID एक संख्या होनी चाहिए (जैसे -1001234567890)।")
    except Exception as e:
        await message.reply_text(f"❌ चैट नहीं मिली। बॉट को चैनल/ग्रुप में एडमिन बनाएँ।\nएरर: {e}")


@Client.on_message(filters.command("clearchat") & filters.private)
async def clear_chat_cmd(bot: Client, message: Message):
    uid = message.from_user.id
    await db.set_dump_chat(uid, None)
    cfg = await get_cfg(uid)
    cfg["default_topic"] = None
    await save_cfg(uid)
    await message.reply_text("✅ टारगेट चैट हटा दी गई। फाइलें अब इसी बॉट चैट में आएँगी।")


@Client.on_message(filters.command("set_topics") & filters.private)
async def set_topics_bulk(bot: Client, message: Message):
    content = _arg(message)
    if not content:
        return await message.reply_text(
            "<b>फॉर्मेट उदाहरण:</b>\n<code>/set_topics\nMaths: https://t.me/c/3635348530/7\n"
            "Reasoning: https://t.me/c/3635348530/8\nHistory: https://t.me/c/3635348530/11</code>",
            parse_mode=HTML,
        )
    mapping = {}
    for line in content.split("\n"):
        if ":" in line:
            name, link = line.split(":", 1)
            m = re.search(r"/(\d+)\s*$", link.strip())
            if m and name.strip():
                mapping[name.strip().lower()] = int(m.group(1))
    if not mapping:
        return await message.reply_text("❌ कोई मान्य टॉपिक लिंक नहीं मिला।")
    uid = message.from_user.id
    cfg = await get_cfg(uid)
    cfg["topic_kw"] = mapping
    await save_cfg(uid)
    res = "<b>✅ टॉपिक कीवर्ड्स सेव हो गए:</b>\n\n"
    for k, v in mapping.items():
        res += f"• <b>{k.title()}</b> ➔ Topic ID: <code>{v}</code>\n"
    await message.reply_text(res, parse_mode=HTML)


@Client.on_message(filters.command("settopic") & filters.private)
async def set_single_topic(bot: Client, message: Message):
    uid = message.from_user.id
    args = message.text.split()
    cfg = await get_cfg(uid)
    if len(args) < 2 or args[1].lower() == "clear":
        cfg["default_topic"] = None
        await save_cfg(uid)
        return await message.reply_text("✅ डिफ़ॉल्ट टॉपिक हटा दिया गया।")
    try:
        cfg["default_topic"] = int(args[1])
    except ValueError:
        return await message.reply_text("❌ कृपया केवल संख्यात्मक ID दें।")
    await save_cfg(uid)
    await message.reply_text(f"✅ डिफ़ॉल्ट टॉपिक सेट: <code>{cfg['default_topic']}</code>", parse_mode=HTML)


@Client.on_message(filters.command("map_topic") & filters.private)
async def map_topic_cmd(bot: Client, message: Message):
    args = message.text.split()
    if len(args) < 3:
        return await message.reply_text(
            "उपयोग: <code>/map_topic &lt;source_topic_id&gt; &lt;target_topic_id&gt;</code>", parse_mode=HTML
        )
    try:
        src, dest = int(args[1]), int(args[2])
    except ValueError:
        return await message.reply_text("❌ दोनों टॉपिक ID संख्या होनी चाहिए।")
    uid = message.from_user.id
    cfg = await get_cfg(uid)
    cfg["topic_direct"][src] = dest
    await save_cfg(uid)
    await message.reply_text(f"✅ टॉपिक मैप: <code>{src}</code> ➔ <code>{dest}</code>", parse_mode=HTML)


@Client.on_message(filters.command("show_topics") & filters.private)
async def show_topics_cmd(bot: Client, message: Message):
    cfg = await get_cfg(message.from_user.id)
    if not cfg["topic_kw"] and not cfg["topic_direct"] and not cfg["default_topic"]:
        return await message.reply_text("ℹ️ अभी कोई टॉपिक सेटिंग नहीं है।")
    res = "📌 <b>सक्रिय टॉपिक सेटिंग:</b>\n"
    if cfg["default_topic"]:
        res += f"\n<b>डिफ़ॉल्ट:</b> <code>{cfg['default_topic']}</code>\n"
    if cfg["topic_kw"]:
        res += "\n<b>कीवर्ड:</b>\n" + "".join(
            f"• <code>{k.title()}</code> ➔ <code>{v}</code>\n" for k, v in cfg["topic_kw"].items()
        )
    if cfg["topic_direct"]:
        res += "\n<b>डायरेक्ट मैप:</b>\n" + "".join(
            f"• <code>{s}</code> ➔ <code>{d}</code>\n" for s, d in cfg["topic_direct"].items()
        )
    await message.reply_text(res, parse_mode=HTML)


@Client.on_message(filters.command("reset_topics") & filters.private)
async def reset_topics_cmd(bot: Client, message: Message):
    uid = message.from_user.id
    cfg = await get_cfg(uid)
    cfg["topic_kw"], cfg["topic_direct"], cfg["default_topic"] = {}, {}, None
    await save_cfg(uid)
    await message.reply_text("🗑️ सभी टॉपिक सेटिंग्स हटा दी गईं।")


# ------------------------------------------------------------------------------
# Thumbnail / caption / prefix / suffix / ads
# ------------------------------------------------------------------------------
@Client.on_message(filters.command("set_thumb") & filters.private)
async def set_thumb_cmd(bot: Client, message: Message):
    reply = message.reply_to_message
    if not reply or not reply.photo:
        return await message.reply_text("⚠️ किसी फोटो पर रिप्लाई करके <code>/set_thumb</code> भेजें।", parse_mode=HTML)
    uid = message.from_user.id
    cfg = await get_cfg(uid)
    cfg["thumb"] = reply.photo.file_id
    await save_cfg(uid)
    await message.reply_text("✅ <b>कस्टम थंबनेल सेव हो गया!</b> (अब वीडियो/डॉक्यूमेंट पर लगेगा)", parse_mode=HTML)


@Client.on_message(filters.command("view_thumb") & filters.private)
async def view_thumb_cmd(bot: Client, message: Message):
    cfg = await get_cfg(message.from_user.id)
    if not cfg["thumb"]:
        return await message.reply_text("❌ कोई कस्टम थंबनेल सेट नहीं है।")
    try:
        await message.reply_photo(photo=cfg["thumb"], caption="🖼️ आपका वर्तमान थंबनेल")
    except Exception:
        await message.reply_text("⚠️ थंबनेल खुल नहीं पाया, कृपया /set_thumb से दोबारा सेट करें।")


@Client.on_message(filters.command("del_thumb") & filters.private)
async def del_thumb_cmd(bot: Client, message: Message):
    uid = message.from_user.id
    cfg = await get_cfg(uid)
    cfg["thumb"] = None
    await save_cfg(uid)
    await message.reply_text("🗑️ कस्टम थंबनेल हटा दिया गया।")


@Client.on_message(filters.command("set_caption") & filters.private)
async def set_caption_cmd(bot: Client, message: Message):
    text = _arg(message)
    if not text:
        return await message.reply_text(
            "<b>उपयोग:</b> <code>/set_caption आपका कैप्शन</code>\n\n"
            "<b>Placeholders:</b>\n• <code>{filename}</code> फाइल का नाम\n• <code>{size}</code> साइज़\n"
            "• <code>{caption}</code> ओरिजिनल कैप्शन (साफ किया हुआ)\n\n"
            "<i>उदाहरण:</i> <code>/set_caption 📚 {filename}\n💾 {size}</code>",
            parse_mode=HTML,
        )
    uid = message.from_user.id
    cfg = await get_cfg(uid)
    cfg["caption"] = text
    await save_cfg(uid)
    await message.reply_text(f"✅ <b>कस्टम कैप्शन सेट हुआ:</b>\n\n{text}", parse_mode=PLAIN)


@Client.on_message(filters.command("see_caption") & filters.private)
async def see_caption_cmd(bot: Client, message: Message):
    cfg = await get_cfg(message.from_user.id)
    if not cfg["caption"]:
        return await message.reply_text("ℹ️ कोई कस्टम कैप्शन सेट नहीं है।")
    await message.reply_text(f"📝 वर्तमान कैप्शन:\n\n{cfg['caption']}", parse_mode=PLAIN)


@Client.on_message(filters.command("del_caption") & filters.private)
async def del_caption_cmd(bot: Client, message: Message):
    uid = message.from_user.id
    cfg = await get_cfg(uid)
    cfg["caption"] = ""
    await save_cfg(uid)
    await message.reply_text("🗑️ कस्टम कैप्शन हटा दिया गया।")


@Client.on_message(filters.command("clean_ads") & filters.private)
async def clean_ads_cmd(bot: Client, message: Message):
    uid = message.from_user.id
    cfg = await get_cfg(uid)
    args = message.text.split()
    if len(args) < 2 or args[1].lower() not in ("on", "off"):
        cur = "ON" if cfg["clean_ads"] else "OFF"
        return await message.reply_text(
            f"उपयोग: <code>/clean_ads on</code> या <code>off</code> (अभी: <b>{cur}</b>)", parse_mode=HTML
        )
    cfg["clean_ads"] = args[1].lower() == "on"
    await save_cfg(uid)
    await message.reply_text(f"✅ Ad क्लीनर: <b>{'ON' if cfg['clean_ads'] else 'OFF'}</b>", parse_mode=HTML)


async def _set_text_field(message: Message, field: str, label: str):
    uid = message.from_user.id
    cfg = await get_cfg(uid)
    text = _arg(message)
    if not text or text.lower() == "clear":
        cfg[field] = ""
        await save_cfg(uid)
        return await message.reply_text(f"✅ {label} हटा दिया गया।")
    cfg[field] = text
    await save_cfg(uid)
    await message.reply_text(f"✅ {label} सेट हुआ:\n\n{text}", parse_mode=PLAIN)


@Client.on_message(filters.command("prefix") & filters.private)
async def prefix_cmd(bot: Client, message: Message):
    await _set_text_field(message, "prefix", "प्रीफिक्स")


@Client.on_message(filters.command("suffix") & filters.private)
async def suffix_cmd(bot: Client, message: Message):
    await _set_text_field(message, "suffix", "सफ़िक्स")


# ------------------------------------------------------------------------------
# Replace / delete words
# ------------------------------------------------------------------------------
def _split_pair(text: str):
    """'old->new' | 'old:new' | 'old new'  ->  (old, new) or None"""
    for sep in ("->", ":"):
        if sep in text:
            old, new = text.split(sep, 1)
            return old.strip(), new.strip()
    parts = text.split(None, 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return None


@Client.on_message(filters.command(["replace", "set_repl_word"]) & filters.private)
async def replace_cmd(bot: Client, message: Message):
    pair = _split_pair(_arg(message))
    if not pair or not pair[0]:
        return await message.reply_text(
            "उपयोग: <code>/replace पुराना:नया</code>\nया <code>/set_repl_word पुराना नया</code>\n"
            "(लिंक के लिए <code>-&gt;</code> इस्तेमाल करें)",
            parse_mode=HTML,
        )
    old, new = pair
    uid = message.from_user.id
    cfg = await get_cfg(uid)
    cfg["replace"][old] = new
    await save_cfg(uid)
    await message.reply_text(f"✅ रिप्लेसमेंट सेट:\n{old}  ➔  {new}", parse_mode=PLAIN)


@Client.on_message(filters.command("rem_repl_word") & filters.private)
async def rem_replace_cmd(bot: Client, message: Message):
    word = _arg(message)
    if not word:
        return await message.reply_text("उपयोग: <code>/rem_repl_word शब्द</code>", parse_mode=HTML)
    uid = message.from_user.id
    cfg = await get_cfg(uid)
    if cfg["replace"].pop(word, None) is None:
        return await message.reply_text("ℹ️ यह शब्द रिप्लेस लिस्ट में नहीं मिला।")
    await save_cfg(uid)
    await message.reply_text(f"🗑️ रिप्लेस लिस्ट से हटाया: {word}", parse_mode=PLAIN)


@Client.on_message(filters.command("clear_replace") & filters.private)
async def clear_replace_cmd(bot: Client, message: Message):
    uid = message.from_user.id
    cfg = await get_cfg(uid)
    cfg["replace"] = {}
    await save_cfg(uid)
    await message.reply_text("🗑️ सभी रिप्लेसमेंट नियम हटा दिए गए।")


@Client.on_message(filters.command("set_del_word") & filters.private)
async def set_del_cmd(bot: Client, message: Message):
    words = [w.strip() for w in _arg(message).split(",") if w.strip()]
    if not words:
        return await message.reply_text(
            "उपयोग: <code>/set_del_word शब्द1, शब्द2</code>\n(कॉमा से अलग करें; स्पेस वाले वाक्य भी चलेंगे)",
            parse_mode=HTML,
        )
    uid = message.from_user.id
    cfg = await get_cfg(uid)
    for w in words:
        if w not in cfg["delete"]:
            cfg["delete"].append(w)
    await save_cfg(uid)
    await message.reply_text("✅ डिलीट लिस्ट में जोड़ा:\n" + "\n".join(f"• {w}" for w in words), parse_mode=PLAIN)


@Client.on_message(filters.command("rem_del_word") & filters.private)
async def rem_del_cmd(bot: Client, message: Message):
    words = [w.strip() for w in _arg(message).split(",") if w.strip()]
    if not words:
        return await message.reply_text("उपयोग: <code>/rem_del_word शब्द</code>", parse_mode=HTML)
    uid = message.from_user.id
    cfg = await get_cfg(uid)
    cfg["delete"] = [w for w in cfg["delete"] if w not in words]
    await save_cfg(uid)
    await message.reply_text("🗑️ डिलीट लिस्ट से हटाया:\n" + "\n".join(f"• {w}" for w in words), parse_mode=PLAIN)


# ------------------------------------------------------------------------------
# Cancel (login in progress OR running batch)
# ------------------------------------------------------------------------------
@Client.on_message(filters.command(["cancel", "cancellogin"]) & filters.private)
async def cancel_task(bot: Client, message: Message):
    from plugins.session import drop_login, remove_keyboard  # shared login state

    uid = message.from_user.id
    did = False
    if await drop_login(uid):
        did = True
        await message.reply_text("❌ <b>लॉगिन प्रोसेस रद्द किया गया।</b>", parse_mode=HTML, reply_markup=remove_keyboard)
    if State.IS_BUSY.get(uid):
        State.IS_BUSY[uid] = False
        did = True
        await message.reply_text("🛑 <b>टास्क रोका जा रहा है...</b>", parse_mode=HTML)
    if not did:
        await message.reply_text("ℹ️ अभी कोई टास्क चालू नहीं है।")


@Client.on_callback_query(filters.regex("^cancel_task$"))
async def cancel_callback(bot: Client, query: CallbackQuery):
    State.IS_BUSY[query.from_user.id] = False
    await query.answer("टास्क रोका जा रहा है...", show_alert=False)


# ------------------------------------------------------------------------------
# Core engine  -  ZERO DISK
#
#   1. copy_message (server side, nothing downloaded) whenever the chat allows copying
#   2. optional: copy through the user's session (USER_SESSION_COPY=True)
#   3. protected chats / bot has no access: read through the user session and
#        - small files  -> held in RAM (BytesIO) and re-sent
#        - big files    -> streamed download->upload pipeline (see streamer.py)
# ------------------------------------------------------------------------------
async def fetch_chunk(clients, chat, ids):
    """Fetch up to 100 messages in ONE request. Returns {id: (message, client_that_fetched_it)}.
    The client matters: file ids are only valid for the session that fetched the message."""
    found = {}
    for cl in clients:
        pending = [i for i in ids if i not in found]
        if not pending:
            break
        for _ in range(3):
            try:
                msgs = await cl.get_messages(chat, pending)
                if not isinstance(msgs, list):
                    msgs = [msgs]
                for m in msgs:
                    if m and not m.empty:
                        found[m.id] = (m, cl)
                break
            except FloodWait as f:
                await asyncio.sleep(f.value + 1)
            except Exception as e:
                logger.info(f"get_messages failed with one client: {e}")
                break
    return found


class Job:
    def __init__(self, bot, uid, chat_id, source_chat, link_topic, target_chat, is_forum,
                 user_client, cfg, progress, total, markup):
        self.bot, self.uid, self.chat_id = bot, uid, chat_id
        self.source_chat, self.link_topic = source_chat, link_topic
        self.target_chat, self.is_forum = target_chat, is_forum
        self.user_client, self.cfg = user_client, cfg
        self.progress, self.total, self.markup = progress, total, markup
        self.ok = self.fail = self.skip = 0
        self.bot_can_copy = True
        # the user's account would post into "Saved Messages" if the target is the user's own chat
        self.user_can_copy = bool(USER_SESSION_COPY and user_client and target_chat != chat_id)
        self.custom_thumb = None  # bytes, kept in RAM
        self.last_error = ""
        self._last_edit = 0.0

    # ---- helpers
    @staticmethod
    def _topic_kw(topic):
        return {"message_thread_id": topic} if topic else {}

    def aborted(self):
        return not State.IS_BUSY.get(self.uid)

    async def call(self, fn, **kw):
        for _ in range(4):
            try:
                return await fn(**kw)
            except FloodWait as f:
                await asyncio.sleep(f.value + 1)
        return await fn(**kw)

    async def tick(self, force=False):
        now = time.time()
        if not force and now - self._last_edit < 4:
            return
        self._last_edit = now
        done = self.ok + self.fail + self.skip
        try:
            await self.progress.edit_text(
                f"⏳ <b>प्रोसेस हो रहा है:</b> <code>{done}/{self.total}</code>\n"
                f"✅ {self.ok}   ⚠️ {self.fail}   ⏭️ {self.skip}",
                parse_mode=HTML, reply_markup=self.markup,
            )
        except Exception:
            pass

    # ---- 1/2. server-side copy
    async def try_copy(self, client, msg, caption, topic):
        kw = dict(chat_id=self.target_chat, from_chat_id=self.source_chat, message_id=msg.id,
                  **self._topic_kw(topic))
        if caption is not None:
            kw.update(caption=caption, parse_mode=PLAIN)
        try:
            await self.call(client.copy_message, **kw)
            return True
        except Exception as e:
            ident = getattr(e, "ID", "")
            if ident in COPY_BLOCKING_ERRORS or isinstance(e, (ValueError, KeyError)):
                if client is self.bot:
                    self.bot_can_copy = False
                else:
                    self.user_can_copy = False
            logger.info(f"copy failed for {msg.id}: {ident or e}")
            return False

    # ---- 3. in-memory / streamed re-upload
    @staticmethod
    def _bio(data: bytes, name: str):
        import io
        bio = io.BytesIO(data)
        bio.name = name
        return bio

    async def _source_thumb(self, reader, msg, mt):
        """Telegram's own video thumbnail (a few KB) as bytes, or None."""
        if mt != "video":
            return None
        thumbs = getattr(msg.video, "thumbs", None)
        if not thumbs:
            return None
        try:
            bio = await reader.download_media(thumbs[0].file_id, in_memory=True)
            return bio.getvalue() if bio else None
        except Exception:
            return None

    async def _prepare(self, reader, msg, mt, file_name):
        """Returns (file_object, thumb_bytes). Small -> BytesIO in RAM, big -> StreamSource."""
        media = getattr(msg, mt)
        size = int(getattr(media, "file_size", 0) or 0)
        thumb = self.custom_thumb or await self._source_thumb(reader, msg, mt)
        if mt in STREAMABLE and size > RAM_FILE_LIMIT_MB * MIB:
            src = StreamSource(reader, msg, file_name, size, abort=self.aborted,
                               workers=STREAM_WORKERS, segment_mb=STREAM_SEGMENT_MB,
                               upload_parallel=UPLOAD_PARALLEL)
            return src, thumb
        bio = await reader.download_media(msg, in_memory=True)
        if bio is None:
            raise RuntimeError("download returned nothing")
        bio.name = file_name if mt in STREAMABLE else (getattr(bio, "name", None) or file_name)
        return bio, thumb

    async def send_file(self, mt, msg, fobj, thumb, caption, topic, file_name):
        kw = dict(chat_id=self.target_chat, **self._topic_kw(topic))
        cap = dict(caption=caption, parse_mode=PLAIN) if caption else {}
        th = self._bio(thumb, "thumb.jpg") if thumb else None
        b = self.bot
        if mt == "video":
            v = msg.video
            return await self.call(b.send_video, video=fobj, file_name=file_name, thumb=th,
                                   duration=v.duration or 0, width=v.width or 0, height=v.height or 0,
                                   supports_streaming=True, **cap, **kw)
        if mt == "animation":
            a = msg.animation
            return await self.call(b.send_animation, animation=fobj, file_name=file_name,
                                   duration=a.duration or 0, width=a.width or 0, height=a.height or 0,
                                   **cap, **kw)
        if mt == "document":
            return await self.call(b.send_document, document=fobj, file_name=file_name,
                                   thumb=self._bio(self.custom_thumb, "thumb.jpg") if self.custom_thumb else None,
                                   **cap, **kw)
        if mt == "photo":
            return await self.call(b.send_photo, photo=fobj, **cap, **kw)
        if mt == "audio":
            a = msg.audio
            return await self.call(b.send_audio, audio=fobj, file_name=file_name, duration=a.duration or 0,
                                   performer=a.performer, title=a.title,
                                   thumb=self._bio(self.custom_thumb, "thumb.jpg") if self.custom_thumb else None,
                                   **cap, **kw)
        if mt == "voice":
            return await self.call(b.send_voice, voice=fobj, duration=msg.voice.duration or 0, **cap, **kw)
        if mt == "video_note":
            n = msg.video_note
            return await self.call(b.send_video_note, video_note=fobj, duration=n.duration or 0,
                                   length=n.length or 0, **kw)
        if mt == "sticker":
            return await self.call(b.send_sticker, sticker=fobj, **kw)
        raise RuntimeError(f"unsupported media: {mt}")

    async def transfer(self, msg, reader, mt, caption, topic):
        """Re-send one media message without ever writing to disk."""
        original_name = default_filename(msg, mt)
        file_name = renamed_name(original_name, self.cfg)
        last = None
        for attempt in range(2):  # one automatic retry for network hiccups
            fobj = None
            try:
                fobj, thumb = await self._prepare(reader, msg, mt, file_name)
                await self.send_file(mt, msg, fobj, thumb, caption, topic, file_name)
                self.ok += 1
                return
            except StreamAborted:
                self.skip += 1
                return
            except asyncio.CancelledError:
                raise
            except Exception as e:
                last = e
                logger.warning(f"transfer of {msg.id} failed (try {attempt + 1}/2): {e}")
                if self.aborted():
                    self.skip += 1
                    return
            finally:
                if hasattr(fobj, "close"):
                    fobj.close()
        self.fail += 1
        self.last_error = f"msg {msg.id}: {last}"
        logger.error(f"Transfer error on {msg.id}: {last}")

    async def handle(self, msg, reader):
        mt = get_media_type(msg)
        if not mt:
            self.skip += 1
            return
        source_topic = self.link_topic or getattr(msg, "message_thread_id", None)
        topic = resolve_target_topic(self.cfg, msg, source_topic)
        topic = topic if (self.is_forum and self.target_chat != self.chat_id) else None

        if mt == "text":
            text = build_text(self.cfg, msg.text or "")[:4096]
            if not text.strip():
                self.skip += 1
                return
            await self.call(self.bot.send_message, chat_id=self.target_chat, text=text,
                            parse_mode=PLAIN, **self._topic_kw(topic))
            self.ok += 1
            return

        name, size = file_info(msg, mt)
        original = msg.caption or ""
        new_caption = build_caption(self.cfg, original, name, size)[:1024]
        changed = new_caption != original.strip()

        if not is_protected(msg):
            cap = new_caption if changed else None
            if self.bot_can_copy and await self.try_copy(self.bot, msg, cap, topic):
                self.ok += 1
                return
            if self.user_can_copy and await self.try_copy(self.user_client, msg, cap, topic):
                self.ok += 1
                return
        await self.transfer(msg, reader, mt, new_caption, topic)

    async def run(self, ids):
        clients = [c for c in (self.user_client, self.bot) if c]
        if self.cfg.get("thumb"):
            try:
                bio = await self.bot.download_media(self.cfg["thumb"], in_memory=True)
                self.custom_thumb = bio.getvalue() if bio else None
            except Exception as e:
                logger.warning(f"Custom thumbnail download failed: {e}")

        for chunk in chunks(ids, 100):
            if self.aborted():
                break
            found = await fetch_chunk(clients, self.source_chat, chunk)
            ordered = [found[i] for i in chunk if i in found]
            self.skip += len(chunk) - len(ordered)
            for msg, reader in ordered:
                if self.aborted():
                    break
                try:
                    await self.handle(msg, reader)
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    self.fail += 1
                    self.last_error = f"msg {msg.id}: {e}"
                    logger.error(f"Error on message {msg.id}: {e}")
                await self.tick()
                await asyncio.sleep(COPY_DELAY)

    async def cleanup(self):
        if self.user_client:
            try:
                await self.user_client.stop()
            except Exception:
                try:
                    await self.user_client.disconnect()
                except Exception:
                    pass


async def open_user_client(uid, session_str):
    """Start the user's Pyrogram session (no update handling = least overhead).
    Returns (client, error_text)."""
    cl = Client(f"user_session_{uid}", session_string=session_str, api_id=API_ID, api_hash=API_HASH,
                in_memory=True, no_updates=True, workers=2, max_concurrent_transmissions=max(STREAM_WORKERS * 2, 8))
    try:
        await cl.start()
        return cl, None
    except SESSION_DEAD_ERRORS as e:
        return None, f"dead:{e.__class__.__name__}"
    except Exception as e:
        logger.warning(f"Session connect error: {e}")
        try:
            await cl.stop()
        except Exception:
            pass
        return None, str(e)


@Client.on_message(filters.text & filters.private & ~filters.regex(r"^/") & ~login_state_filter)
async def universal_forwarder(bot: Client, message: Message):
    text = message.text.strip()
    raw = next((t for t in text.split() if "t.me/" in t), None)
    if not raw:
        return

    uid = message.from_user.id
    if State.IS_BUSY.get(uid):
        return await message.reply_text("⚠️ एक टास्क पहले से चालू है। रोकने के लिए <code>/cancel</code> भेजें।", parse_mode=HTML)

    base, to_id = split_range(raw.split("?")[0])
    source_chat, from_id, link_topic = parse_tg_link(base)
    if not source_chat or not from_id:
        return await message.reply_text("❌ <b>अमान्य टेलीग्राम लिंक।</b> सही लिंक भेजें।", parse_mode=HTML)
    to_id = max(to_id or from_id, from_id)
    if to_id - from_id + 1 > MAX_BATCH:
        return await message.reply_text(f"⚠️ एक बार में अधिकतम <code>{MAX_BATCH}</code> मैसेज की रेंज दें।", parse_mode=HTML)

    cfg = await get_cfg(uid)
    dump_chat = await db.get_dump_chat(uid)
    target_chat = dump_chat or message.chat.id
    is_forum = False
    if target_chat != message.chat.id:
        try:
            t_obj = await bot.get_chat(target_chat)
            is_forum = bool(getattr(t_obj, "is_forum", False))
        except Exception as e:
            return await message.reply_text(
                f"❌ टारगेट चैट खुल नहीं रही। बॉट को वहाँ एडमिन बनाएँ या /clearchat करें।\nएरर: {e}")

    user_client = None
    own_session = await db.get_session(uid)
    session_str = own_session or STRING_SESSION
    if session_str:
        user_client, err = await open_user_client(uid, session_str)
        if err and err.startswith("dead:"):
            if own_session:
                await db.set_session(uid, None)  # useless now - make the user log in again
            return await message.reply_text(
                "⚠️ <b>आपका लॉगिन सेशन खत्म/रद्द हो चुका है।</b>\n/login करके दोबारा लॉगिन करें।", parse_mode=HTML)

    if isinstance(source_chat, int) and not user_client:
        return await message.reply_text(
            "🔒 <b>प्राइवेट चैनल/ग्रुप के लिए पहले <code>/login</code> करें।</b>", parse_mode=HTML)

    State.IS_BUSY[uid] = True
    markup = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancel", callback_data="cancel_task")]])
    total = to_id - from_id + 1
    progress = await message.reply_text(
        f"🚀 <b>टास्क शुरू:</b> <code>{from_id}</code> से <code>{to_id}</code>", parse_mode=HTML, reply_markup=markup)

    job = Job(bot, uid, message.chat.id, source_chat, link_topic, target_chat, is_forum,
              user_client, cfg, progress, total, markup)
    started = time.time()
    stopped = False
    try:
        await job.run(list(range(from_id, to_id + 1)))
        stopped = not State.IS_BUSY.get(uid)
    except Exception as e:
        logger.error(f"Job crashed: {e}")
        await message.reply_text(f"❌ टास्क में एरर आया: <code>{html.escape(str(e))}</code>", parse_mode=HTML)
    finally:
        State.IS_BUSY[uid] = False
        await job.cleanup()

    took = int(time.time() - started)
    hint = ""
    if job.ok == 0 and job.fail == 0 and user_client:
        hint = "\n\n💡 कुछ नहीं मिला? लिंक जाँचें, या <code>/logout</code> → <code>/login</code> करें।"
    if job.last_error:
        hint += f"\n\n⚠️ <b>आख़िरी एरर:</b> <code>{html.escape(job.last_error[:300])}</code>"
    title = "🛑 <b>टास्क रोका गया।</b>" if stopped else "✅ <b>टास्क पूरा हुआ!</b>"
    try:
        await progress.edit_text(
            f"{title}\n\n✅ भेजे गए: <code>{job.ok}</code>\n⚠️ फेल: <code>{job.fail}</code>\n"
            f"⏭️ स्किप: <code>{job.skip}</code>\n⏱️ समय: <code>{took}s</code>{hint}",
            parse_mode=HTML)
    except Exception:
        pass
