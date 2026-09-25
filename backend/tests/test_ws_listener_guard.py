"""Listener setup is idempotent (single delivery after restarts)."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.notifications import notification_manager
from app.websocket import notification_listener, setup_websocket_listener


async def _run():
    # Reset global listener state for isolation.
    async with notification_manager._lock:
        notification_manager._listeners.discard(notification_listener)
    await setup_websocket_listener()
    await setup_websocket_listener()
    async with notification_manager._lock:
        n = sum(1 for li in notification_manager._listeners if li is notification_listener)
    assert n == 1, f"listener registered {n}x"
    print("PASS listener guard")


def test_listener_setup_idempotent():
    asyncio.run(_run())


if __name__ == "__main__":
    test_listener_setup_idempotent()
