import datetime

import motor.motor_asyncio

from config import DB_NAME, DB_URI
from logger import LOGGER

logger = LOGGER(__name__)

MAX_DAYS = 36500  # 100 years - protects timedelta from overflow


def _utcnow():
    """Naive UTC datetime (this is how MongoDB hands datetimes back with motor's defaults)."""
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


class Database:
    def __init__(self, uri, database_name):
        # an empty DB_URI must not crash at import time: bot.py reports it with a clear message
        self._client = motor.motor_asyncio.AsyncIOMotorClient(uri or "mongodb://localhost:27017")
        self.db = self._client[database_name]
        self.col = self.db.users

    @staticmethod
    def utcnow():
        return _utcnow()

    async def ensure_indexes(self):
        try:
            await self.col.create_index("id", unique=True)
        except Exception as e:
            logger.warning(f"Index creation skipped: {e}")

    # ------------------------------------------------------------ users
    @staticmethod
    def new_user(id, name):
        return dict(session=None, dump_chat=None, cfg={})

    async def add_user(self, id, name):
        """Create the user doc if missing; always keep the display name fresh."""
        await self.col.update_one(
            {"id": int(id)},
            {"$setOnInsert": self.new_user(id, name), "$set": {"name": name}},
            upsert=True,
        )

    async def is_user_exist(self, id):
        return bool(await self.col.find_one({"id": int(id)}, {"_id": 1}))

    async def total_users_count(self):
        return await self.col.count_documents({})

    async def get_all_users(self):
        return self.col.find({})

    async def delete_user(self, user_id, keep_access=True):
        """Remove a user doc. By default users who hold premium/access are kept
        (a user who blocked the bot must not silently lose a paid plan or session)."""
        query = {"id": int(user_id)}
        if keep_access:
            query["has_access"] = {"$ne": True}
        await self.col.delete_many(query)
        logger.info(f"User deleted from DB: {user_id}")

    # ---------------------------------------------------------- session
    async def set_session(self, id, session):
        await self.col.update_one({"id": int(id)}, {"$set": {"session": session}}, upsert=True)

    async def get_session(self, id):
        user = await self.col.find_one({"id": int(id)}, {"session": 1})
        return user.get("session") if user else None

    # ------------------------------------------------------- dump chat
    async def set_dump_chat(self, id, chat_id):
        if chat_id is None:
            await self.col.update_one({"id": int(id)}, {"$unset": {"dump_chat": ""}})
        else:
            await self.col.update_one(
                {"id": int(id)}, {"$set": {"dump_chat": int(chat_id)}}, upsert=True
            )

    async def get_dump_chat(self, id):
        user = await self.col.find_one({"id": int(id)}, {"dump_chat": 1})
        return user.get("dump_chat") if user else None

    # --------------------------------------------- per-user settings (cfg)
    async def get_cfg(self, id):
        user = await self.col.find_one({"id": int(id)}, {"cfg": 1})
        return (user or {}).get("cfg") or {}

    async def set_cfg(self, id, cfg):
        await self.col.update_one({"id": int(id)}, {"$set": {"cfg": cfg}}, upsert=True)

    # ----------------------------------------------------------- access
    async def grant_access(self, id, days=None, extend=True):
        """Grant premium.

        days=None -> permanent. With days: if the user still has an active, time-limited plan
        the days are ADDED to the remaining time (extend=True), otherwise they start from now.
        Returns the expiry datetime (UTC, naive) or None for permanent access.
        """
        until = None
        if days:
            days = min(int(days), MAX_DAYS)
            base = _utcnow()
            if extend:
                ok, current = await self.get_access(id)
                if ok and current and current > base:
                    base = current
            until = base + datetime.timedelta(days=days)
        await self.col.update_one(
            {"id": int(id)},
            {
                "$set": {"has_access": True, "access_until": until},
                "$unset": {"is_banned": ""},
                "$setOnInsert": self.new_user(id, None),
            },
            upsert=True,
        )
        logger.info(f"Access granted: {id} until {until}")
        return until

    async def revoke_access(self, id):
        await self.col.update_one(
            {"id": int(id)}, {"$set": {"has_access": False}, "$unset": {"access_until": ""}}
        )
        logger.info(f"Access revoked: {id}")

    async def get_access(self, id):
        """Returns (allowed: bool, until: datetime|None). Expired plans are switched off."""
        user = await self.col.find_one(
            {"id": int(id)}, {"has_access": 1, "access_until": 1, "is_banned": 1}
        )
        if not user or user.get("is_banned") or not user.get("has_access"):
            return False, None
        until = user.get("access_until")
        if until and until <= _utcnow():
            await self.col.update_one({"id": int(id)}, {"$set": {"has_access": False}})
            return False, until
        return True, until

    async def list_access(self):
        cursor = self.col.find({"has_access": True}, {"id": 1, "name": 1, "access_until": 1})
        return [u async for u in cursor]

    # -------------------------------------------------------------- ban
    async def ban_user(self, id):
        await self.col.update_one({"id": int(id)}, {"$set": {"is_banned": True}}, upsert=True)
        logger.warning(f"User banned: {id}")

    async def unban_user(self, id):
        await self.col.update_one({"id": int(id)}, {"$set": {"is_banned": False}})
        logger.info(f"User unbanned: {id}")

    async def is_banned(self, id):
        user = await self.col.find_one({"id": int(id)}, {"is_banned": 1})
        return bool(user and user.get("is_banned"))


db = Database(DB_URI, DB_NAME)
