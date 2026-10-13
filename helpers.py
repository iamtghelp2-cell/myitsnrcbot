"""Pure helper functions (no Telegram / database imports) - easy to test."""

import os
import re

MEDIA_TYPES = ("video", "animation", "document", "photo", "audio", "voice", "video_note", "sticker")


# ------------------------------------------------------------------ links
def parse_tg_link(url: str):
    """Returns (chat, message_id, topic_id). chat is int (private) or str (public)."""
    url = url.split("?")[0].strip()

    m = re.search(r"t\.me/c/(\d+)/(\d+)/(\d+)", url)  # private forum topic
    if m:
        return int("-100" + m.group(1)), int(m.group(3)), int(m.group(2))

    m = re.search(r"t\.me/c/(\d+)/(\d+)", url)  # private normal
    if m:
        return int("-100" + m.group(1)), int(m.group(2)), None

    m = re.search(r"t\.me/([^/]+)/(\d+)/(\d+)", url)  # public forum topic
    if m:
        return m.group(1), int(m.group(3)), int(m.group(2))

    m = re.search(r"t\.me/([^/]+)/(\d+)", url)  # public normal
    if m:
        return m.group(1), int(m.group(2)), None

    return None, None, None


def split_range(raw: str):
    """'https://t.me/c/1/10-20' -> ('https://t.me/c/1/10', 20); no range -> (raw, None)."""
    m = re.match(r"^(.*/\d+)-(\d+)$", raw)
    if m:
        return m.group(1), int(m.group(2))
    return raw, None


# ------------------------------------------------------------------ media
def get_media_type(msg):
    for name in MEDIA_TYPES:
        if getattr(msg, name, None):
            return name
    if getattr(msg, "text", None):
        return "text"
    return None


def humansize(num):
    num = float(num or 0)
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024:
            return f"{num:.1f} {unit}" if unit != "B" else f"{int(num)} B"
        num /= 1024
    return f"{num:.2f} TB"


def file_info(msg, media_type):
    media = getattr(msg, media_type, None)
    name = getattr(media, "file_name", None) or ""
    size = humansize(getattr(media, "file_size", 0))
    return name, size


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# ------------------------------------------------------------------ text rules
def clean_ads_text(text: str) -> str:
    text = re.sub(r"(https?://\S+|t\.me/\S+)", "", text)
    text = re.sub(r"@[a-zA-Z0-9_]+", "", text)
    text = re.sub(r"Join\s*:\s*\S+", "", text, flags=re.IGNORECASE)
    return re.sub(r"[ \t]{2,}", " ", text)


def process_text(cfg: dict, text: str) -> str:
    """replace-words -> delete-words -> ad-cleaner.
    Replacement targets are protected, so a new @username / link survives the ad-cleaner."""
    if not text:
        return ""
    tokens = []
    for old, new in cfg.get("replace", {}).items():
        if old and old in text:
            key = f"\u27e6{len(tokens)}\u27e7"
            text = text.replace(old, key)
            tokens.append((key, new))
    for word in cfg.get("delete", []):
        if word:
            text = text.replace(word, "")
    if cfg.get("clean_ads"):
        text = clean_ads_text(text)
    for key, new in tokens:
        text = text.replace(key, new)
    return text


def _wrap(cfg: dict, body: str) -> str:
    prefix, suffix = cfg.get("prefix", ""), cfg.get("suffix", "")
    if prefix:
        body = f"{prefix}\n\n{body}" if body else prefix
    if suffix:
        body = f"{body}\n\n{suffix}" if body else suffix
    return body.strip()


def build_caption(cfg: dict, original: str, filename: str = "", size: str = "") -> str:
    """Final caption for a media message.
    Custom caption supports {filename}, {size} and {caption} (the cleaned original caption)."""
    orig = process_text(cfg, original or "").strip()
    if cfg.get("caption"):
        stem = os.path.splitext(filename)[0] if filename else ""
        body = (
            cfg["caption"]
            .replace("{filename}", process_text(cfg, stem))
            .replace("{size}", size)
            .replace("{caption}", orig)
        )
    else:
        body = orig
    return _wrap(cfg, body)


def build_text(cfg: dict, text: str) -> str:
    return _wrap(cfg, process_text(cfg, text or "").strip())


def renamed_path(path: str, cfg: dict) -> str:
    """Apply replace/delete/ad-clean rules to the file name (not the extension)."""
    folder, base = os.path.split(path)
    stem, ext = os.path.splitext(base)
    new_stem = re.sub(r'[\\/:*?"<>|]', "_", process_text(cfg, stem)).strip(" ._-")
    if not new_stem or new_stem == stem:
        return path
    new_path = os.path.join(folder, new_stem + ext)
    try:
        os.replace(path, new_path)
        return new_path
    except OSError:
        return path


def renamed_name(name: str, cfg: dict) -> str:
    """In-memory twin of renamed_path(): apply replace/delete/ad-clean rules to a file NAME
    (extension is kept). Returns the original name when nothing changes."""
    if not name:
        return name
    stem, ext = os.path.splitext(name)
    new_stem = re.sub(r'[\\/:*?"<>|]', "_", process_text(cfg, stem)).strip(" ._-")
    if not new_stem or new_stem == stem:
        return name
    return new_stem + ext


def default_filename(msg, media_type: str) -> str:
    """A usable file name (with extension) for media that has none, e.g. most videos."""
    import mimetypes
    media = getattr(msg, media_type, None)
    name = getattr(media, "file_name", None)
    if name:
        return name
    mime = getattr(media, "mime_type", None) or ""
    ext = mimetypes.guess_extension(mime) if mime else None
    ext = ext or {"video": ".mp4", "animation": ".mp4", "audio": ".mp3", "voice": ".ogg",
                  "photo": ".jpg", "sticker": ".webp", "video_note": ".mp4"}.get(media_type, ".bin")
    return f"{media_type}_{getattr(msg, 'id', 0)}{ext}"


# ------------------------------------------------------------------ topics
def resolve_target_topic(cfg: dict, msg, source_topic=None):
    direct = cfg.get("topic_direct", {})
    if source_topic and source_topic in direct:
        return direct[source_topic]

    keywords = cfg.get("topic_kw", {})
    if keywords:
        haystack = (getattr(msg, "caption", None) or getattr(msg, "text", None) or "").lower()
        for kind in ("video", "document", "audio"):
            media = getattr(msg, kind, None)
            fname = getattr(media, "file_name", None) if media else None
            if fname:
                haystack += " " + fname.lower()
        for key, topic_id in keywords.items():
            if key and key in haystack:
                return topic_id

    if source_topic:
        return source_topic
    return cfg.get("default_topic")


def is_protected(msg) -> bool:
    chat = getattr(msg, "chat", None)
    return bool(getattr(msg, "has_protected_content", False) or getattr(chat, "has_protected_content", False))
