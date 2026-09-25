"""Ingest prefetches dedupe + latest rows: correct outcomes, bounded SELECTs."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.core.sync import sync_service
from app.models.project import Project


def _ev(i, pid="pp", name=None, ts=None):
    return {
        "event_id": f"pf-{i}", "entity_type": "projects",
        "entity_public_id": pid, "operation": "create",
        "payload": {"public_id": pid, "name": name or f"N{i}"},
        "logical_ts": ts if ts is not None else i, "source_node_id": "node-a",
    }


async def _run():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    # 30 distinct projects + 2 versions of one entity (latest-in-batch must win)
    # + 1 stale loser.
    events = [_ev(i, pid=f"pp-{i}") for i in range(30)]
    events += [_ev(100, pid="dup", name="old", ts=50),
               _ev(101, pid="dup", name="new", ts=60)]
    async with Session() as db:
        selects = []

        def _listener(conn, cursor, statement, *a, **k):
            selects.append(statement)

        event.listen(engine.sync_engine, "before_cursor_execute", _listener)
        try:
            r = await sync_service.ingest_remote_events(db, peer_node_id="peer", events=events)
            await db.commit()
            await sync_service.ingest_remote_events(db, peer_node_id="peer", events=[
                _ev(102, pid="dup", name="stale", ts=40)])
            await db.commit()
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", _listener)
        n_select = sum(1 for s in selects if str(s).lstrip().upper().startswith("SELECT"))
        assert r.applied == 32, f"applied={r.applied}"
        name = (await db.execute(select(Project.name).where(Project.public_id == "dup"))).scalar()
        assert name == "new", f"latest-in-batch must win, got {name!r}"
        # Measured pre-fix: 100 SELECTs (dedupe+latest per event plus applier
        # lookups). Prefetch cuts the per-event pair to ~2 SELECTs per batch.
        assert n_select < 60, f"too many SELECTs: {n_select}"
    await engine.dispose()
    print(f"PASS prefetch ({n_select} SELECTs)")


def test_prefetch_correct_and_bounded():
    asyncio.run(_run())


if __name__ == "__main__":
    test_prefetch_correct_and_bounded()
