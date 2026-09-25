"""Listeners run concurrently; failures stay isolated."""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.notifications import NotificationManager


async def _one_round():
    mgr = NotificationManager()
    marks = {}

    async def _slow(event_type, data):
        marks["slow_start"] = time.monotonic()
        await asyncio.sleep(0.2)
        marks["slow_end"] = time.monotonic()

    async def _fast(event_type, data):
        marks["fast_start"] = time.monotonic()
        marks["fast_end"] = time.monotonic()

    async def _boom(event_type, data):
        raise RuntimeError("listener boom")

    await mgr.add_listener(_slow)
    await mgr.add_listener(_fast)
    await mgr.add_listener(_boom)
    await asyncio.wait_for(mgr.notify("t", {}), timeout=5.0)
    assert "slow_end" in marks and "fast_end" in marks  # all completed
    return marks["fast_start"] < marks["slow_end"]


async def _run():
    # Overlap is impossible under sequential await; 10 rounds force the
    # slow-first order pre-fix (P(10x fast-first) ~= 0).
    overlaps = 0
    for _ in range(10):
        overlaps += await _one_round()
    assert overlaps == 10, f"overlapped {overlaps}/10 rounds"


def test_notify_concurrency():
    asyncio.run(_run())


if __name__ == "__main__":
    test_notify_concurrency()
