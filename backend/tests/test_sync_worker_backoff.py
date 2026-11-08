"""A failing peer is skipped until its backoff elapses; it never sleeps inside the cycle."""
import asyncio
import os
import sys
import time
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.core.sync_worker as worker_mod


class _Resp:
    def __init__(self, status, payload=None):
        self.status = status
        self._payload = payload or {"node_id": "n", "events": []}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return "boom"


class _Http:
    def __init__(self):
        self.gets = []

    def get(self, url, headers=None):
        self.gets.append(url)
        return _Resp(500 if "bad-peer" in url else 200)

    def post(self, url, json=None, headers=None):
        return _Resp(200, {"applied": 0, "skipped": 0, "latest_logical_ts": 0})


async def _run():
    from contextlib import asynccontextmanager
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
    from app.database import Base
    import app.models  # noqa: F401

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session_ctx():
        async with maker() as db:
            yield db

    w = worker_mod.SyncWorker()
    http = _Http()
    peers = ["http://bad-peer:1", "http://good-peer:2"]

    async def fake_session():
        return http

    async def fake_peers():
        return peers

    real_sleep = asyncio.sleep
    slept = []

    async def spy_sleep(d, *a, **k):
        slept.append(d)
        await real_sleep(0)

    with patch.object(worker_mod, "async_session_maker", session_ctx), \
         patch.object(w, "_ensure_session", fake_session), \
         patch.object(w, "_resolve_peers", fake_peers), \
         patch.object(worker_mod.settings, "SYNC_ENABLED", True), \
         patch.object(worker_mod.settings, "SYNC_EVENT_RETENTION_DAYS", 0), \
         patch.object(worker_mod.asyncio, "sleep", spy_sleep):
        t0 = time.monotonic()
        await w.run_once()
        assert time.monotonic() - t0 < 2
        assert not [d for d in slept if d >= 1], f"backoff slept inside the cycle: {slept}"
        # Both peers were tried on the first cycle.
        assert any("bad-peer" in u for u in http.gets) and any("good-peer" in u for u in http.gets)

        # Second cycle: the failing peer is not due, the healthy one still syncs.
        http.gets.clear()
        await w.run_once()
        assert not any("bad-peer" in u for u in http.gets), http.gets
        assert any("good-peer" in u for u in http.gets), http.gets

        # A manual run bypasses the backoff.
        http.gets.clear()
        await w.run_once(force=True)
        assert any("bad-peer" in u for u in http.gets), http.gets

        # Once due again, the peer is retried.
        http.gets.clear()
        w._peer_next_attempt["http://bad-peer:1"] = time.monotonic() - 1
        await w.run_once()
        assert any("bad-peer" in u for u in http.gets), http.gets
    await engine.dispose()


def test_failing_peer_backoff_does_not_block_cycle():
    asyncio.run(_run())
