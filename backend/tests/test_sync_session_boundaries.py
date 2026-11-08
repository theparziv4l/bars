"""No DB session is open during sync HTTP calls (pull GET or push POST)."""
import asyncio
import os
import sys
from contextlib import asynccontextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
import app.core.sync_worker as worker_mod
from app.core.sync import sync_service
from app.models.project import Project


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return ""


class _StubHttp:
    """Records how many DB sessions are open whenever HTTP fires."""
    def __init__(self, open_counter, events):
        self.open_counter = open_counter
        self.events = events
        self.get_open = None
        self.post_open = None

    def get(self, url, headers=None):
        self.get_open = self.open_counter[0]
        return _Resp({"node_id": "peer", "events": self.events})

    def post(self, url, json=None, headers=None):
        self.post_open = self.open_counter[0]
        return _Resp({"applied": 1, "skipped": 0, "latest_logical_ts": 5,
                       "min_deferred_ts": None})


async def _run():
    from unittest.mock import patch
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    real_maker = async_sessionmaker(engine, expire_on_commit=False)
    open_counter = [0]

    @asynccontextmanager
    async def tracking_maker():
        open_counter[0] += 1
        try:
            async with real_maker() as db:
                yield db
        finally:
            open_counter[0] -= 1

    async with real_maker() as db:
        db.add(Project(public_id="lp", name="Local"))
        await db.commit()
        await sync_service.record_event(
            db, entity_type="projects", entity_public_id="lp", operation="create",
            payload={"public_id": "lp", "name": "Local"},
            logical_ts=5, source_node_id="node-a", event_id="local-1")
        await db.commit()

    pull_events = [{
        "event_id": "rem-1", "entity_type": "projects", "entity_public_id": "rp",
        "operation": "create", "payload": {"public_id": "rp", "name": "Remote"},
        "logical_ts": 7, "source_node_id": "peer", "created_at": "2026-01-01T00:00:00",
        "seq": 3,
    }]
    stub = _StubHttp(open_counter, pull_events)
    w = worker_mod.SyncWorker()

    async def fake_ensure():
        return stub

    with patch.object(worker_mod, "async_session_maker", tracking_maker), \
         patch.object(w, "_ensure_session", fake_ensure):
        with patch.object(worker_mod.settings, "SYNC_ENABLED", True):
            await w._sync_peer("http://peer:8000")

    assert stub.get_open == 0, f"GET fired with {stub.get_open} DB sessions open"
    assert stub.post_open == 0, f"POST fired with {stub.post_open} DB sessions open"
    # And the cycle still did its job: pull cursor at the remote event's seq (3);
    # push cursor at the latest LOCAL seq — the ingested remote row is recorded
    # locally (id 2, after local-1), so it relays onward too.
    async with real_maker() as db:
        assert await sync_service.get_cursor(db, "http://peer:8000", "pull_seq") == 3
        assert await sync_service.get_cursor(db, "http://peer:8000", "push_seq") == 2
    await engine.dispose()
    print("PASS session boundaries")


def test_no_db_session_across_http():
    asyncio.run(_run())


if __name__ == "__main__":
    test_no_db_session_across_http()
