"""PubSub: per-channel fan-out, no leak without subscribers."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.pubsub import PubSubBackend


async def _collect(backend, channel, n, timeout=5.0):
    out = []

    async def _run():
        async for msg in backend.subscribe(channel):
            out.append(msg.payload)
            if len(out) >= n:
                break

    await asyncio.wait_for(_run(), timeout)
    return out


async def _wait_subscribed(backend, channel):
    for _ in range(100):
        if backend._local_subscriber_count(channel) > 0:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"no subscriber attached for {channel}")


async def _run():
    backend = PubSubBackend()
    # Publish with no subscribers: nothing accumulates.
    await backend.publish("a", {"v": 1})
    assert backend._local_subscriber_count("a") == 0
    assert backend._local_pending("a") == 0
    # Two channels: each subscriber gets only its own messages.
    task_a = asyncio.create_task(_collect(backend, "a", 2))
    task_b = asyncio.create_task(_collect(backend, "b", 1))
    await _wait_subscribed(backend, "a")
    await _wait_subscribed(backend, "b")
    await backend.publish("a", {"v": 1})
    await backend.publish("b", {"v": 2})
    await backend.publish("a", {"v": 3})
    got_a, got_b = await asyncio.gather(task_a, task_b)
    assert [m["v"] for m in got_a] == [1, 3], got_a
    assert [m["v"] for m in got_b] == [2], got_b


def test_pubsub_fanout():
    asyncio.run(_run())


if __name__ == "__main__":
    test_pubsub_fanout()
