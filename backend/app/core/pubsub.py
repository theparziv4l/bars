"""
Lightweight pub/sub abstraction to allow NotificationManager to work
across multiple backend processes using Redis when configured, and to
fall back to in-process broadcasting otherwise.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, AsyncIterator, Optional

import os

try:
    import aioredis  # type: ignore
except Exception:  # pragma: no cover
    aioredis = None


@dataclass
class PubSubMessage:
    channel: str
    payload: Any


class PubSubBackend:
    def __init__(self) -> None:
        self._local_subs: dict[str, set[asyncio.Queue]] = {}
        self._subs_lock = asyncio.Lock()
        self._redis_dsn: Optional[str] = os.getenv("BARS_REDIS_URL")
        self._redis: Any = None

    def _local_subscriber_count(self, channel: str) -> int:
        return len(self._local_subs.get(channel, ()))

    def _local_pending(self, channel: str) -> int:
        return sum(q.qsize() for q in self._local_subs.get(channel, ()))

    async def _ensure_redis(self) -> None:
        if not self._redis_dsn or not aioredis:
            return
        if self._redis is None:
            self._redis = await aioredis.from_url(self._redis_dsn)

    async def publish(self, channel: str, payload: Any) -> None:
        msg = PubSubMessage(channel=channel, payload=payload)
        async with self._subs_lock:
            queues = list(self._local_subs.get(channel, ()))
        for q in queues:
            if q.full():
                try:
                    q.get_nowait()  # drop oldest, keep newest (bounded backpressure)
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(msg)
            except asyncio.QueueFull:
                pass

        if self._redis_dsn and aioredis:
            await self._ensure_redis()
            if self._redis is not None:
                await self._redis.publish(channel, json.dumps(payload))

    async def subscribe(self, channel: str) -> AsyncIterator[PubSubMessage]:
        if self._redis_dsn and aioredis:
            await self._ensure_redis()
            if self._redis is not None:
                pubsub = self._redis.pubsub()
                await pubsub.subscribe(channel)
                try:
                    async for raw_msg in pubsub.listen():
                        if raw_msg["type"] == "message":
                            try:
                                payload = json.loads(raw_msg["data"])
                            except (json.JSONDecodeError, TypeError):
                                payload = raw_msg["data"]
                            yield PubSubMessage(channel=channel, payload=payload)
                finally:
                    await pubsub.unsubscribe(channel)
                return

        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        async with self._subs_lock:
            self._local_subs.setdefault(channel, set()).add(q)
        try:
            while True:
                yield await q.get()
        finally:
            async with self._subs_lock:
                self._local_subs.get(channel, set()).discard(q)
                if not self._local_subs.get(channel):
                    self._local_subs.pop(channel, None)


pubsub_backend = PubSubBackend()

