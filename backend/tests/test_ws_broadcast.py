"""Broadcast isolates slow clients; bad messages don't kill connections."""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import WebSocketDisconnect

from app.websocket import ConnectionManager, handle_websocket


class _FakeWS:
    def __init__(self, incoming=None):
        self.sent = []
        self.incoming = list(incoming or [])
        self.accepted = False
        self.closed = None
        self.headers = {}
        self.query_params = {}

    async def accept(self):
        self.accepted = True

    async def send_json(self, message):
        self.sent.append(message)

    async def receive_text(self):
        if not self.incoming:
            raise WebSocketDisconnect()
        item = self.incoming.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def close(self, code=1000):
        self.closed = code


class _SlowWS(_FakeWS):
    async def send_json(self, message):
        await asyncio.sleep(30)


async def _run_broadcast():
    mgr = ConnectionManager()
    fast, slow = _FakeWS(), _SlowWS()
    await mgr.connect(fast, "c1")
    await mgr.connect(slow, "c1")
    await asyncio.wait_for(mgr.broadcast({"t": 1}, "c1", timeout=0.2), timeout=5.0)
    assert fast.sent == [{"t": 1}], fast.sent
    async with mgr._lock:
        remaining = set(mgr.active_connections.get("c1", set()))
    assert slow not in remaining and fast in remaining
    # Empty channels are pruned.
    await mgr.disconnect(fast, "c1")
    async with mgr._lock:
        assert "c1" not in mgr.active_connections


async def _run_malformed():
    from app.config import settings
    old = settings.WS_REQUIRE_AUTH
    settings.WS_REQUIRE_AUTH = False
    try:
        ws = _FakeWS(incoming=["not-json{{{", json.dumps({"type": "ping"})])
        await handle_websocket(ws, "c2")
        kinds = [m.get("type") for m in ws.sent]
        assert "error" in kinds, ws.sent
        assert "pong" in kinds, ws.sent  # survived the bad message
    finally:
        settings.WS_REQUIRE_AUTH = old


def test_ws_broadcast_isolation():
    asyncio.run(_run_broadcast())


def test_ws_malformed_survives():
    asyncio.run(_run_malformed())


if __name__ == "__main__":
    test_ws_broadcast_isolation()
    test_ws_malformed_survives()
