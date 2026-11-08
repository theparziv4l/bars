"""Pull/push cursors use the local insertion sequence, not logical_ts."""
import asyncio
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
import app.api.sync as sync_api
import app.core.sync_worker as worker_mod
from app.core.sync import SyncService, sync_service
from app.core.sync_worker import _capped_push_seq


async def _mk():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _rec(db, eid, ts, pid="x", etype="hosts"):
    return await sync_service.record_event(
        db, entity_type=etype, entity_public_id=pid, operation="create",
        payload={"public_id": pid}, logical_ts=ts, source_node_id="n", event_id=eid)


class _Req:
    headers = {"X-Sync-Token": "tok"}


async def _late_commit_and_relay():
    engine, maker = await _mk()
    async with maker() as db:
        e1 = await _rec(db, "late-b", 200, "b")          # committed first, high ts
        await db.commit()
        got = await sync_service.pull_events(db, since_seq=0, limit=10)
        cursor = got[-1].id
        assert [e.event_id for e in got] == ["late-b"]

        # Stamped earlier (ts=100) but committed later: logical_ts cursor skips it,
        # the sequence cursor does not.
        await _rec(db, "late-a", 100, "a")
        await db.commit()
        by_ts = await sync_service.pull_events(db, since_logical_ts=200, limit=10)
        assert by_ts == [], "sanity: the old logical_ts cursor would lose this event"
        by_seq = await sync_service.pull_events(db, since_seq=cursor, limit=10)
        assert [e.event_id for e in by_seq] == ["late-a"]
        cursor = by_seq[-1].id

        # Relay: an event from a third node with an OLD logical_ts arrives via ingest.
        svc = SyncService()
        with patch.object(SyncService, "_mkdir_projects_for_events", _noop):
            await svc.ingest_remote_events(db, peer_node_id="third", events=[{
                "event_id": "relayed", "entity_type": "projects", "entity_public_id": "rp",
                "operation": "create", "payload": {"public_id": "rp", "name": "R"},
                "logical_ts": 50, "source_node_id": "third"}])
            await db.commit()
        relayed = await sync_service.pull_events(db, since_seq=cursor, limit=10)
        assert [e.event_id for e in relayed] == ["relayed"]

        # Project filter still applies in seq mode; legacy param still works.
        await sync_service.record_event(
            db, entity_type="hosts", entity_public_id="h", operation="create",
            payload={"public_id": "h", "project_public_id": "P"}, logical_ts=1,
            source_node_id="n", event_id="scoped")
        await db.commit()
        scoped = await sync_service.pull_events(db, since_seq=0, limit=10, project_public_id="P")
        assert [e.event_id for e in scoped] == ["scoped"]
        legacy = await sync_service.pull_events(db, since_logical_ts=0, limit=10)
        assert len(legacy) == 4
    await engine.dispose()


async def _noop(self, events):
    return None


def test_seq_cursor_catches_late_commit_and_relayed_events():
    asyncio.run(_late_commit_and_relay())


async def _api_pull():
    engine, maker = await _mk()
    async with maker() as db:
        await _rec(db, "a", 300, "a")
        await _rec(db, "b", 100, "b")
        await db.commit()
        with patch.object(sync_api.settings, "SYNC_ENABLED", True), \
             patch.object(sync_api.settings, "SYNC_PEER_TOKEN", "tok"):
            resp = await sync_api.pull_events(
                _Req(), since_logical_ts=0, since_seq=None, limit=10,
                project_public_id=None, db=db)
            # legacy mode: ordered by logical_ts
            assert [e.event_id for e in resp.events] == ["b", "a"]
            seq_resp = await sync_api.pull_events(
                _Req(), since_logical_ts=0, since_seq=1, limit=10,
                project_public_id=None, db=db)
            assert [e.event_id for e in seq_resp.events] == ["b"]
            assert seq_resp.events[0].seq == 2
            assert seq_resp.latest_seq == 2 and seq_resp.max_seq == 2
            empty = await sync_api.pull_events(
                _Req(), since_logical_ts=0, since_seq=2, limit=10,
                project_public_id=None, db=db)
            assert empty.events == [] and empty.latest_seq == 2 and empty.max_seq == 2
    await engine.dispose()


def test_pull_endpoint_seq_param_and_response_fields():
    asyncio.run(_api_pull())


async def _ingest_seq_cursor():
    engine, maker = await _mk()
    svc = SyncService()
    orphan = {"event_id": "o", "entity_type": "services", "entity_public_id": "s",
              "operation": "create", "payload": {"public_id": "s", "host_public_id": "ghost", "port": 1},
              "logical_ts": 5, "source_node_id": "p", "seq": 7}
    fine = {"event_id": "f", "entity_type": "projects", "entity_public_id": "pp",
            "operation": "create", "payload": {"public_id": "pp", "name": "P"},
            "logical_ts": 9, "source_node_id": "p", "seq": 8}
    with patch.object(SyncService, "_mkdir_projects_for_events", _noop):
        async with maker() as db:
            await svc.ingest_remote_events(db, peer_node_id="p", events=[orphan, fine], cursor_key="ck")
            await db.commit()
            # Deferred seq=7 holds the cursor at 6 (seq 8 re-pulled, deduped next time).
            assert await svc.get_cursor(db, "ck", "pull_seq") == 6
            fine2 = {**fine, "event_id": "f2", "entity_public_id": "pp2",
                     "payload": {"public_id": "pp2", "name": "P2"}, "seq": 9}
            await svc.ingest_remote_events(db, peer_node_id="p", events=[fine2], cursor_key="ck")
            await db.commit()
    await engine.dispose()


def test_ingest_writes_seq_cursor_capped_by_deferral():
    asyncio.run(_ingest_seq_cursor())


class _E:
    def __init__(self, id, ts):
        self.id, self.logical_ts = id, ts


def test_push_seq_cap():
    evs = [_E(10, 300), _E(11, 100), _E(12, 400)]
    assert _capped_push_seq(evs, None) == 12
    # receiver deferred something with ts 300: hold at the first event with ts >= 300
    assert _capped_push_seq(evs, 300) == 9
    assert _capped_push_seq(evs, 400) == 11
    assert _capped_push_seq(evs, 100) == 9


class _Resp:
    status = 200

    def __init__(self, payload):
        self._p = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def json(self):
        return self._p

    async def text(self):
        return ""


class _Http:
    def __init__(self):
        self.urls = []

    def get(self, url, headers=None):
        self.urls.append(url)
        return _Resp({"node_id": "peer", "events": [], "max_seq": 3, "latest_seq": 50})

    def post(self, url, json=None, headers=None):
        return _Resp({"applied": 0, "skipped": 0, "latest_logical_ts": 0})


async def _worker_reset():
    from contextlib import asynccontextmanager
    engine, maker = await _mk()

    @asynccontextmanager
    async def ctx():
        async with maker() as db:
            yield db

    async with maker() as db:
        await sync_service.set_cursor(db, peer_node_id="http://p:1", direction="pull_seq", logical_ts=50)
        await db.commit()
    w = worker_mod.SyncWorker()
    http = _Http()

    async def fake_session():
        return http

    with patch.object(worker_mod, "async_session_maker", ctx), \
         patch.object(w, "_ensure_session", fake_session), \
         patch.object(worker_mod.settings, "SYNC_ENABLED", True):
        await w._sync_peer("http://p:1")
    assert "since_seq=50" in http.urls[0], http.urls
    async with maker() as db:
        assert await sync_service.get_cursor(db, "http://p:1", "pull_seq") == 0
    await engine.dispose()


def test_worker_resets_cursor_when_peer_log_restarted():
    asyncio.run(_worker_reset())
