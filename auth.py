"""Who may use the bot: sudo users + admins/owner + users granted premium (with optional expiry)."""

import time

from config import ADMINS, OWNER_IDS, SUDO_USERS
from database.db import db
from logger import LOGGER

logger = LOGGER(__name__)

_CACHE = {}  # user_id -> (allowed, until(datetime|None), checked_at)
_TTL = 30  # seconds


def is_owner(user_id: int) -> bool:
    return user_id in OWNER_IDS


def is_admin(user_id: int) -> bool:
    return user_id in ADMINS


def is_sudo(user_id: int) -> bool:
    """Fixed (config-level) users: they never expire and cannot be removed from the bot."""
    return user_id in SUDO_USERS or user_id in ADMINS or user_id in OWNER_IDS


async def is_allowed(user_id: int) -> bool:
    if is_sudo(user_id):
        return True
    now = time.time()
    cached = _CACHE.get(user_id)
    if cached and now - cached[2] < _TTL:
        allowed, until, _ = cached
        # a cached "yes" must still respect the premium expiry time (else fall through and re-check)
        if not (allowed and until and until <= db.utcnow()):
            return allowed
    try:
        ok, until = await db.get_access(user_id)
    except Exception as e:
        logger.error(f"Access check failed for {user_id}: {e}")
        return False  # fail closed
    _CACHE[user_id] = (ok, until, now)
    return ok


def invalidate(user_id: int):
    _CACHE.pop(user_id, None)
