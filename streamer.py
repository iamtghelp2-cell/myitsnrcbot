"""Zero-disk streaming transfer.

Big files are moved from the source chat to the target chat WITHOUT touching the disk and
without holding the whole file in RAM:

    source (user session)                                     bot
    ┌────────────────────────┐   ordered 8 MB segments   ┌───────────────────────────┐
    │ N parallel downloaders │ ───────────────────────►  │ 512 KB parts, M in flight │ ──► Telegram
    └────────────────────────┘   (bounded look-ahead)    └───────────────────────────┘

Memory per transfer is roughly  (STREAM_WORKERS + 1) * segment size + UPLOAD_PARALLEL * 512 KB.

Usage: pass a ``StreamSource`` as the file argument of ``send_video`` / ``send_document`` / ...
on a client whose ``save_file`` is routed through ``save_stream`` (see ``Bot.save_file`` in bot.py).
"""

import asyncio
import logging
import math

from pyrogram import raw
from pyrogram.errors import FloodWait
from pyrogram.session import Session

log = logging.getLogger(__name__)

MIB = 1024 * 1024
CHUNK = MIB            # Telegram download chunk (stream_media granularity)
PART_SIZE = 512 * 1024  # Telegram upload part size
BIG_FILE = 10 * MIB     # SaveBigFilePart must be used above this size


class StreamAborted(Exception):
    """Raised inside a transfer when the user pressed /cancel."""


class StreamSource:
    """A not-yet-downloaded Telegram media that can be uploaded straight from the network.

    reader  : the Client (user session) that fetched `message` (file ids are per-session)
    message : pyrogram Message holding the media
    name    : file name (pyrogram reads `.name` of file-like objects)
    size    : exact size in bytes (from the message)
    abort   : optional callable -> True when the transfer should stop
    """

    def __init__(self, reader, message, name, size, abort=None,
                 workers=4, segment_mb=8, upload_parallel=8):
        self.reader = reader
        self.message = message
        self.name = name
        self.size = int(size)
        self.abort = abort
        self.workers = max(int(workers), 1)
        self.segment_chunks = max(int(segment_mb), 1)  # 1 chunk = 1 MiB
        self.upload_parallel = max(int(upload_parallel), 1)

    # ------------------------------------------------------------------ download side
    def _check_abort(self):
        if self.abort and self.abort():
            raise StreamAborted()

    async def _fetch_segment(self, index: int) -> bytes:
        offset = index * self.segment_chunks  # in 1 MiB chunks
        expected = min(self.size - offset * CHUNK, self.segment_chunks * CHUNK)
        last_error = None
        for attempt in range(4):
            self._check_abort()
            try:
                buf = bytearray()
                async for chunk in self.reader.stream_media(
                    self.message, limit=self.segment_chunks, offset=offset
                ):
                    buf += chunk
                if len(buf) != expected:
                    raise IOError(f"segment {index}: got {len(buf)} bytes, expected {expected}")
                return bytes(buf)
            except FloodWait as f:
                last_error = f
                await asyncio.sleep(f.value + 1)
            except (StreamAborted, asyncio.CancelledError):
                raise
            except Exception as e:  # network hiccup, file reference refresh, ...
                last_error = e
                log.warning(f"download segment {index} failed (try {attempt + 1}/4): {e}")
                await asyncio.sleep(1 + attempt)
        raise last_error

    async def segments(self):
        """Yield the file as ordered byte segments, downloading `workers` of them in parallel."""
        total = math.ceil(self.size / (self.segment_chunks * CHUNK))
        tasks = {}
        launched = 0
        try:
            for index in range(total):
                while launched < total and launched < index + self.workers:
                    tasks[launched] = asyncio.create_task(self._fetch_segment(launched))
                    launched += 1
                data = await tasks.pop(index)
                yield data
        finally:
            for t in tasks.values():
                t.cancel()
            if tasks:
                await asyncio.gather(*tasks.values(), return_exceptions=True)


# ---------------------------------------------------------------------- upload side
async def save_stream(client, src: StreamSource):
    """Upload a StreamSource with `client` (the bot) and return an InputFileBig."""
    size = src.size
    if size <= BIG_FILE:
        raise ValueError("StreamSource is only for files bigger than 10 MB")
    limit_mib = 4000 if getattr(client.me, "is_premium", False) else 2000
    if size > limit_mib * MIB:
        raise ValueError(f"File is bigger than the {limit_mib} MiB upload limit")

    total_parts = math.ceil(size / PART_SIZE)

    async with client.save_file_semaphore:
        session = Session(
            client, await client.storage.dc_id(), await client.storage.auth_key(),
            await client.storage.test_mode(), is_media=True,
        )
        file_id = client.rnd_id()
        slots = asyncio.Semaphore(src.upload_parallel)
        inflight, errors = set(), []
        sent_bytes = 0

        async def send_part(number: int, data: bytes):
            try:
                for attempt in range(4):
                    try:
                        ok = await session.invoke(raw.functions.upload.SaveBigFilePart(
                            file_id=file_id, file_part=number,
                            file_total_parts=total_parts, bytes=data))
                        if ok is False:
                            raise IOError(f"part {number} rejected")
                        return
                    except FloodWait as f:
                        await asyncio.sleep(f.value + 1)
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        if attempt == 3:
                            raise
                        log.warning(f"upload part {number} failed (try {attempt + 1}/4): {e}")
                        await asyncio.sleep(1 + attempt)
                raise IOError(f"part {number}: too many flood waits")
            finally:
                slots.release()

        def on_done(task):
            inflight.discard(task)
            if not task.cancelled() and task.exception() is not None:
                errors.append(task.exception())

        async def launch(number: int, data: bytes):
            await slots.acquire()
            if errors:
                slots.release()
                raise errors[0]
            task = asyncio.create_task(send_part(number, data))
            inflight.add(task)
            task.add_done_callback(on_done)

        try:
            await session.start()
            buf = bytearray()
            part_no = 0
            async for segment in src.segments():
                buf += segment
                while len(buf) >= PART_SIZE:
                    part = bytes(buf[:PART_SIZE])
                    del buf[:PART_SIZE]
                    await launch(part_no, part)
                    part_no += 1
                    sent_bytes += len(part)
            if buf:
                await launch(part_no, bytes(buf))
                part_no += 1
                sent_bytes += len(buf)

            if inflight:
                await asyncio.gather(*list(inflight), return_exceptions=True)
            if errors:
                raise errors[0]
            if sent_bytes != size or part_no != total_parts:
                raise IOError(f"size mismatch: streamed {sent_bytes} of {size} bytes")
        except BaseException:
            for t in list(inflight):
                t.cancel()
            if inflight:
                await asyncio.gather(*list(inflight), return_exceptions=True)
            raise
        finally:
            try:
                await session.stop()
            except Exception:
                pass

    return raw.types.InputFileBig(id=file_id, parts=total_parts, name=src.name)
