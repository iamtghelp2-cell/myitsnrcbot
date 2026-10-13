"""
Itsnrcbot configuration.
Secrets (BOT_TOKEN, API_HASH, DB_URI ...) go in a .env file or environment
variables on your server - never hard-code them and never share them in chat.
"""

import os


def _load_dotenv(path=".env"):
    """Tiny .env loader (no extra dependency)."""
    if not os.path.isfile(path):
        return
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()


def _int_list(value):
    return [int(x) for x in value.replace(" ", "").split(",") if x.lstrip("-").isdigit()]


# ---------------- Telegram credentials ----------------
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
API_ID = int(os.environ.get("API_ID", "0") or 0)
API_HASH = os.environ.get("API_HASH", "")

# Optional: one global user-session string used when a user has not logged in
STRING_SESSION = os.environ.get("STRING_SESSION", "") or None

# ---------------- Branding ----------------
BOT_NAME = os.environ.get("BOT_NAME", "Itsnrcbot")
OWNER_USERNAME = os.environ.get("OWNER_USERNAME", "IamTgBhai").lstrip("@")
CHANNEL_LINK = os.environ.get("CHANNEL_LINK", "https://t.me/iamtghelp")

# ---------------- Access control ----------------
# Owner: full admin (add/remove users, ban, broadcast ...)
OWNER_IDS = [7951185227]

# These IDs can always use the bot (they never expire). Extra sudo users can be
# added through the SUDO_USERS env variable (comma separated).
SUDO_USERS = sorted(set([7951185227, 7732591949, 5886149022] + _int_list(os.environ.get("SUDO_USERS", ""))))

# Admins can manage access (/add_premium, /ban, /broadcast ...). Owner is always an admin.
# Extra admins can be added through the ADMINS env variable (optional).
ADMINS = sorted(set(OWNER_IDS + _int_list(os.environ.get("ADMINS", ""))))

# ---------------- Database ----------------
DB_URI = os.environ.get("DB_URI", "")
DB_NAME = os.environ.get("DB_NAME", "Itsnrcbot")

# ---------------- Logging ----------------
# Channel ID for start/stop + new-user logs. 0 = disabled.
LOG_CHANNEL = int(os.environ.get("LOG_CHANNEL", "0") or 0)
ERROR_MESSAGE = os.environ.get("ERROR_MESSAGE", "True").lower() == "true"

# ---------------- Speed / behaviour ----------------
COPY_DELAY = float(os.environ.get("COPY_DELAY", "0.3"))   # pause between messages (seconds)
MAX_BATCH = int(os.environ.get("MAX_BATCH", "10000"))     # max messages in one range

# ---------------- Keep-alive web server (only for Render/Heroku style hosts) ----------------
# Auto-enabled on hosts that inject a PORT variable (Koyeb, Render, Heroku...) - they need an open
# HTTP port for the health check, otherwise the deployment is marked "Error" and killed.
KEEP_ALIVE = os.environ.get("KEEP_ALIVE", "True" if os.environ.get("PORT") else "False").lower() == "true"

# ---------------- Zero-disk transfer engine ----------------
# Nothing is ever written to the VPS disk. Small files are held in RAM; big files are
# streamed segment-by-segment (download -> upload pipeline with bounded memory).
RAM_FILE_LIMIT_MB = max(int(os.environ.get("RAM_FILE_LIMIT_MB", "20")), 11)  # files <= this are held fully in RAM
STREAM_WORKERS = max(int(os.environ.get("STREAM_WORKERS", "4")), 1)          # parallel download segments
STREAM_SEGMENT_MB = max(int(os.environ.get("STREAM_SEGMENT_MB", "8")), 1)    # size of one download segment
UPLOAD_PARALLEL = max(int(os.environ.get("UPLOAD_PARALLEL", "8")), 1)        # parallel 512 KB upload parts
# Optional: let the user's session copy (server side) into the target chat when the bot itself
# cannot reach the source. Only works if the logged-in account can post in the target chat.
USER_SESSION_COPY = os.environ.get("USER_SESSION_COPY", "False").lower() == "true"
