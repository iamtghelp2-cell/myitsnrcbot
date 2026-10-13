"""Run this BEFORE starting the bot (any server):   python check_setup.py

It checks folder/file names (typos like "pluging" / "dttabase"), Python version, installed
packages, environment variables and the MongoDB connection, and tells you exactly what to fix.
"""

import asyncio
import difflib
import importlib
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
os.chdir(ROOT)
sys.path.insert(0, ROOT)

REQUIRED_FILES = [
    "bot.py", "config.py", "auth.py", "helpers.py", "streamer.py", "logger.py", "keep_alive.py",
    "requirements.txt", "Dockerfile", "Procfile",
    "database/__init__.py", "database/db.py",
    "plugins/__init__.py", "plugins/start.py", "plugins/access.py", "plugins/admin.py",
    "plugins/broadcast.py", "plugins/session.py",
]
EXPECTED_DIRS = {"plugins", "database"}
problems, warnings = [], []


def ok(msg):
    print(f"  OK   {msg}")


def bad(msg):
    problems.append(msg)
    print(f"  FAIL {msg}")


print("1) Python version")
if sys.version_info >= (3, 9):
    ok(f"Python {sys.version.split()[0]}")
else:
    bad(f"Python {sys.version.split()[0]} is too old - use Python 3.9 - 3.12")

print("2) Folder and file names")
for d in sorted(x for x in os.listdir(".") if os.path.isdir(x) and not x.startswith((".", "__", "venv"))):
    if d not in EXPECTED_DIRS:
        near = difflib.get_close_matches(d, EXPECTED_DIRS, n=1, cutoff=0.6)
        bad(f"folder '{d}' is not expected" + (f" - rename it to '{near[0]}'" if near else ""))
for f in REQUIRED_FILES:
    if os.path.isfile(f):
        ok(f)
    else:
        bad(f"missing file: {f}")

print("3) Python packages")
for mod, pip in (("pyrogram", "pyrofork"), ("motor", "motor"), ("dns", "dnspython")):
    try:
        importlib.import_module(mod)
        ok(pip)
    except Exception:
        bad(f"package '{pip}' not installed - run: pip install -r requirements.txt")
try:
    importlib.import_module("tgcrypto")
    ok("TgCrypto (fast encryption)")
except Exception:
    print("  WARN TgCrypto not installed - bot works but slower (pip install TgCrypto)")

print("4) Settings (.env or environment variables)")
try:
    import config
    for name in ("BOT_TOKEN", "API_ID", "API_HASH", "DB_URI"):
        if getattr(config, name):
            ok(name)
        else:
            bad(f"{name} is empty - set it in .env or in your host's Environment settings")
    print(f"       OWNER_USERNAME = @{config.OWNER_USERNAME}   CHANNEL_LINK = {config.CHANNEL_LINK}")
    print(f"       KEEP_ALIVE = {config.KEEP_ALIVE}  (must be True on Koyeb/Render/Heroku web services; PORT={os.environ.get('PORT', 'not set')})")
except Exception as e:
    bad(f"config.py could not be loaded: {e}")

print("5) MongoDB connection")
if not problems:
    async def ping():
        from database.db import db
        await asyncio.wait_for(db._client.admin.command("ping"), 15)
    try:
        asyncio.run(ping())
        ok("MongoDB reachable")
    except Exception as e:
        bad(f"cannot connect to MongoDB: {e} (check DB_URI, password and 'Network Access -> 0.0.0.0/0' in Atlas)")
else:
    print("  skipped (fix the problems above first)")

print()
if problems:
    print(f"{len(problems)} problem(s) found - fix them, then run this check again.")
    sys.exit(1)
print("ALL GOOD - start the bot with:  python bot.py")
