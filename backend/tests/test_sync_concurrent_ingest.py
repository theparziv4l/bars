"""Concurrent ingestion serializes per peer; unique races degrade to skip."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.core.sync import sync_service
from app.models.project import Project


def _ev(i):
    return {
        "event_id": f"conc-{i}", "entity_type": "projects",
        "entity_public_id": f"cp-{i}", "operation": "create",
        "payload": {"public_id": f"cp-{i}", "name": f"C{i}"},
        "logical_ts": 100 + i, "source_node_id": "node-a",
    }


async def _run():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async def one_batch():
        async with Session() as db:
            r = await sync_service.ingest_remote_events(
                db, peer_node_id="same-peer", events=[_ev(i) for i in range(10)])
            await db.commit()
            return r

    r1, r2 = await asyncio.gather(one_batch(), one_batch())
    async with Session() as db:
        n = (await db.execute(
            select(func.count()).select_from(Project).where(Project.name.like("C%")))).scalar()
        assert n == 10, f"exactly one copy of each row, got {n}"
    assert r1.applied + r2.applied == 10, f"applied={r1.applied}+{r2.applied}"
    await engine.dispose()
    print("PASS concurrent ingest")


def test_concurrent_same_peer_ingest():
    asyncio.run(_run())


async def _savepoint_run():
    from unittest.mock import patch
    from sqlalchemy.exc import IntegrityError
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    real_apply = sync_service._apply_event

    async def flaky_apply(db, **kw):
        if kw.get("payload", {}).get("public_id") == "cp-boom":
            raise IntegrityError("INSERT ...", {}, Exception("boom"))
        return await real_apply(db, **kw)

    async with Session() as db:
        events = [_ev(50), _ev(51)]
        events[0]["entity_public_id"] = "cp-boom"
        events[0]["payload"]["public_id"] = "cp-boom"
        with patch.object(sync_service, "_apply_event", side_effect=flaky_apply):
            r = await sync_service.ingest_remote_events(
                db, peer_node_id="peer", events=events)
            await db.commit()
        assert r.applied == 1 and r.skipped == 1, f"applied={r.applied} skipped={r.skipped}"
        n = (await db.execute(select(func.count()).select_from(Project))).scalar()
        assert n == 1, f"sibling must survive, projects={n}"
    await engine.dispose()
    print("PASS savepoint isolates bad event")


def test_savepoint_isolates_bad_event():
    asyncio.run(_savepoint_run())


if __name__ == "__main__":
    test_concurrent_same_peer_ingest()
    test_savepoint_isolates_bad_event()
