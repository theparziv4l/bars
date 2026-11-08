"""Push ingest and the worker's pull ingest serialize on the same per-peer lock key."""
import asyncio
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
import app.core.sync_worker as worker_mod
import app.api.sync as sync_api
from app.core.sync import sync_service
from app.models.sync import SyncCursor
from app.schemas.sync import SyncEventMessage, SyncPushRequest


class _Resp:
    def __init__(self, payload):
        self.status = 200
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return ""


class _Http:
    def __init__(self, events):
        self.events = events

    def get(self, url, headers=None):
        return _Resp({"node_id": "peer-node", "events": self.events})

    def post(self, url, json=None, headers=None):
        return _Resp({"applied": 0, "skipped": 0, "latest_logical_ts": 0})


class _Req:
    headers = {"X-Sync-Token": "tok"}


async def _run():
    from contextlib import asynccontextmanager
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def session_ctx():
        async with maker() as db:
            yield db

    keys = []
    real = sync_service._ingest_lock_for

    async def spy(peer_node_id):
        keys.append(peer_node_id)
        return await real(peer_node_id)

    ev = {"event_id": "r1", "entity_type": "projects", "entity_public_id": "rp",
          "operation": "create", "payload": {"public_id": "rp", "name": "R"},
          "logical_ts": 7, "source_node_id": "peer-node", "created_at": "2026-01-01T00:00:00"}
    w = worker_mod.SyncWorker()
    http = _Http([ev])

    async def fake_session():
        return http

    with patch.object(sync_service, "_ingest_lock_for", spy), \
         patch.object(worker_mod, "async_session_maker", session_ctx), \
         patch.object(w, "_ensure_session", fake_session), \
         patch.object(worker_mod.settings, "SYNC_ENABLED", True):
        await w._sync_peer("http://peer:8000")
    assert keys == ["peer-node"], f"worker pull must lock on the peer's node id, got {keys}"

    keys.clear()
    push_ev = SyncEventMessage(
        event_id="r2", entity_type="projects", entity_public_id="rp2", operation="create",
        payload={"public_id": "rp2", "name": "R2"}, logical_ts=8,
        source_node_id="peer-node", created_at="2026-01-01T00:00:00")
    with patch.object(sync_service, "_ingest_lock_for", spy), \
         patch.object(sync_api.settings, "SYNC_ENABLED", True), \
         patch.object(sync_api.settings, "SYNC_PEER_TOKEN", "tok"):
        async with maker() as db:
            await sync_api.push_events(
                SyncPushRequest(source_node_id="peer-node", events=[push_ev]), _Req(), db)
            await db.commit()
    assert keys == ["peer-node"], keys

    async with maker() as db:
        rows = (await db.execute(select(SyncCursor))).scalars().all()
        peers = {(r.peer_node_id, r.direction) for r in rows}
        assert ("http://peer:8000", "pull") in peers, peers
        # A push never moves a pull cursor (and certainly not under the node id key).
        assert not any(k == "peer-node" for k, _ in peers), peers
    await engine.dispose()


def test_push_and_pull_share_lock_key():
    asyncio.run(_run())
