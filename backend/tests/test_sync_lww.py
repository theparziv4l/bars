"""Same-timestamp concurrent writes converge deterministically regardless of order."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.core.sync import sync_service
from app.models.project import Project


def _ev(eid, node, name):
    return {
        "event_id": eid, "entity_type": "projects", "entity_public_id": "p1",
        "operation": "update", "payload": {"public_id": "p1", "name": name},
        "logical_ts": 100, "source_node_id": node,
    }


async def _ingest_in_order(order):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        db.add(Project(public_id="p1", name="seed"))
        await db.commit()
        await sync_service.ingest_remote_events(db, peer_node_id="peer", events=order)
        await db.commit()
        name = (await db.execute(select(Project.name).where(Project.public_id == "p1"))).scalar()
    await engine.dispose()
    return name


async def _run():
    a = _ev("event-aaa", "node-x", "from-x")
    b = _ev("event-bbb", "node-x", "from-x-newer-id")
    # Same ts AND same node: old code treats second arrival as stale (equal key -> skip),
    # so the winner depends on arrival order. New code must pick deterministically.
    name1 = await _ingest_in_order([a, b])
    name2 = await _ingest_in_order([b, a])
    assert name1 == name2 == "from-x-newer-id", f"order-dependent: {name1!r} vs {name2!r}"
    print("PASS lww deterministic")


def test_same_ts_same_node_converges():
    asyncio.run(_run())


if __name__ == "__main__":
    test_same_ts_same_node_converges()
