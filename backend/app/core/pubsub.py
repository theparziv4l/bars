"""Local in-process pub/sub fan-out for notifications (single process)."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, AsyncIterator


@dataclass
class PubSubMessage:
    channel: str
    payload: Any


class PubSubBackend:
    def __init__(self) -> None:
        self._local_subs: dict[str, set[asyncio.Queue]] = {}
        self._subs_lock = asyncio.Lock()

    def _local_subscriber_count(self, channel: str) -> int:
        return len(self._local_subs.get(channel, ()))

    def _local_pending(self, channel: str) -> int:
        return sum(q.qsize() for q in self._local_subs.get(channel, ()))

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

    async def subscribe(self, channel: str) -> AsyncIterator[PubSubMessage]:
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

