"""A never-satisfiable deferred event stops pinning the cursor after N cycles (parked)."""
import asyncio
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
import app.core.sync as sync_mod
from app.core.sync import SyncService
from app.models.host import Host, Service
from app.models.project import Project


def _ev(eid, etype, pid, payload, ts):
    return {"event_id": eid, "entity_type": etype, "entity_public_id": pid,
            "operation": "create", "payload": payload, "logical_ts": ts,
            "source_node_id": "peer"}


async def _run():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    svc = SyncService()
    orphan = _ev("svc-orphan", "services", "svc-1",
                 {"public_id": "svc-1", "host_public_id": "late-host", "port": 22}, 5)
    later = _ev("proj-1", "projects", "p1", {"public_id": "p1", "name": "P"}, 10)

    with patch.object(sync_mod, "_MAX_DEFERRAL_CYCLES", 3), \
         patch.object(SyncService, "_mkdir_projects_for_events", _noop):
        held = []
        for _ in range(5):
            async with maker() as db:
                res = await svc.ingest_remote_events(
                    db, peer_node_id="peer", events=[orphan, later], cursor_key="ck")
                await db.commit()
                held.append(res.min_deferred_ts)
        # First 3 cycles hold the cursor below the orphan; afterwards it is parked.
        assert held[:3] == [5, 5, 5], held
        assert held[3] is None and held[4] is None, held
        assert "svc-orphan" in svc._parked
        async with maker() as db:
            assert await svc.get_cursor(db, "ck", "pull") == 10

        # The parent finally arrives (from anywhere): the parked event resolves.
        host_ev = _ev("host-ev", "hosts", "late-host",
                      {"public_id": "late-host", "project_public_id": "p1",
                       "ip_address": "10.0.0.9"}, 20)
        async with maker() as db:
            await svc.ingest_remote_events(db, peer_node_id="peer", events=[host_ev], cursor_key="ck")
            await db.commit()
            s = (await db.execute(select(Service))).scalar_one()
            h = (await db.execute(select(Host))).scalar_one()
            assert s.host_id == h.id
        assert not svc._parked and not svc._deferral_attempts
    await engine.dispose()


async def _noop(self, events):
    return None


def test_deferral_cap_parks_event_and_late_parent_resolves_it():
    asyncio.run(_run())
