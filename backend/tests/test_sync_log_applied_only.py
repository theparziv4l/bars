"""Stale LWW-losers are not recorded in the event log."""
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
from app.models.sync import SyncEvent


def _ev(eid, ts, name):
    return {
        "event_id": eid, "entity_type": "projects", "entity_public_id": "p1",
        "operation": "update", "payload": {"public_id": "p1", "name": name},
        "logical_ts": ts, "source_node_id": "node-a",
    }


async def _run():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        db.add(Project(public_id="p1", name="seed"))
        await db.commit()
        await sync_service.ingest_remote_events(
            db, peer_node_id="peer", events=[_ev("win", 100, "winner")])
        await db.commit()
        await sync_service.ingest_remote_events(
            db, peer_node_id="peer", events=[_ev("stale", 50, "loser")])
        await db.commit()
        n = (await db.execute(select(func.count()).select_from(SyncEvent))).scalar()
        assert n == 1, f"loser must not be logged, got {n} rows"
        name = (await db.execute(select(Project.name).where(Project.public_id == "p1"))).scalar()
        assert name == "winner"
        # Replay of the winner still dedupes (idempotent).
        r = await sync_service.ingest_remote_events(
            db, peer_node_id="peer", events=[_ev("win", 100, "winner")])
        assert r.skipped == 1 and r.applied == 0
    await engine.dispose()
    print("PASS log applied only")


def test_loser_not_logged_replay_idempotent():
    asyncio.run(_run())


if __name__ == "__main__":
    test_loser_not_logged_replay_idempotent()
