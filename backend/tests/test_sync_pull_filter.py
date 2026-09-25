"""Project-scoped pull filters server-side; retention purge deletes only old rows."""
import asyncio
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.core.sync import sync_service, purge_old_events


async def _run():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        # Sparse: 28 proj-B rows first, then 2 proj-A rows at the END.
        # Pre-fix over-fetch (limit*5=10) never reaches them -> starvation.
        for i in range(28):
            await sync_service.record_event(
                db, entity_type="hosts", entity_public_id=f"b{i}", operation="create",
                payload={"public_id": f"b{i}", "project_public_id": "proj-B"},
                logical_ts=i + 1, source_node_id="node-a", event_id=f"eb{i}")
        for i in range(2):
            await sync_service.record_event(
                db, entity_type="hosts", entity_public_id=f"a{i}", operation="create",
                payload={"public_id": f"a{i}", "project_public_id": "proj-A"},
                logical_ts=29 + i, source_node_id="node-a", event_id=f"ea{i}")
        await db.commit()
        got = await sync_service.pull_events(
            db, since_logical_ts=0, limit=2, project_public_id="proj-A")
        assert [e.entity_public_id for e in got] == ["a0", "a1"], \
            f"SQL-side filter + limit, got {[e.entity_public_id for e in got]}"
        # Retention: backdate half the rows, purge, only old ones go.
        from app.models.sync import SyncEvent
        rows = (await db.execute(select(SyncEvent))).scalars().all()
        old_cutoff = datetime.utcnow() - timedelta(days=31)
        for r in rows[:15]:
            r.created_at = old_cutoff
        await db.commit()
        deleted = await purge_old_events(db, days=30)
        await db.commit()
        assert deleted == 15, f"expected 15 purged, got {deleted}"
        left = (await db.execute(select(SyncEvent))).scalars().all()
        assert len(left) == 15
    await engine.dispose()
    print("PASS pull filter + purge")


def test_project_filter_server_side_and_purge():
    asyncio.run(_run())


if __name__ == "__main__":
    test_project_filter_server_side_and_purge()
