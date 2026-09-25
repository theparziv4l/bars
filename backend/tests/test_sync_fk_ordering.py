"""Out-of-order sync ingestion defers (not drops) children whose parents arrive later."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.core.sync import sync_service
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.host import Host, Service
from app.models.project import Project


def _ev(ts, etype, epid, op, payload, node="node-a"):
    return {
        "event_id": f"e-{etype}-{epid}-{ts}",
        "entity_type": etype, "entity_public_id": epid, "operation": op,
        "payload": payload, "logical_ts": ts, "source_node_id": node,
    }


async def _run():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        # Child timestamps SMALLER than parents: sort puts children first.
        events = [
            _ev(1, "services", "svc-1", "create",
               {"public_id": "svc-1", "host_public_id": "host-1", "port": 445}),
            _ev(2, "hosts", "host-1", "create",
               {"public_id": "host-1", "project_public_id": "proj-1", "ip_address": "10.0.0.1"}),
            _ev(3, "checklist_items", "item-1", "create",
               {"public_id": "item-1", "group_public_id": "grp-1", "name": "i"}),
            _ev(4, "checklist_groups", "grp-1", "create",
               {"public_id": "grp-1", "project_public_id": "proj-1", "name": "g"}),
            _ev(5, "projects", "proj-1", "create",
               {"public_id": "proj-1", "name": "P"}),
        ]
        # Pass 1: children defer; pass 2 (re-ingest incl. parents): everything applies.
        r1 = await sync_service.ingest_remote_events(db, peer_node_id="peer", events=events)
        await db.commit()
        r2 = await sync_service.ingest_remote_events(db, peer_node_id="peer", events=events)
        await db.commit()
        assert (await db.execute(select(Host))).scalars().one().ip_address == "10.0.0.1"
        assert (await db.execute(select(Service))).scalars().one().port == 445
        assert (await db.execute(select(ChecklistItem))).scalars().one().name == "i"
        grp = (await db.execute(select(ChecklistGroup))).scalars().one()
        assert grp.project_id is not None, "group must link to project, not orphan with NULL"
        assert r1.applied + r2.applied >= 5
    await engine.dispose()
    print("PASS test_sync_fk_ordering")


def test_out_of_order_batch_applies_fully():
    asyncio.run(_run())


if __name__ == "__main__":
    test_out_of_order_batch_applies_fully()
