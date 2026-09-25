"""Cross-node duplicate findings converge; replay never regresses occurrences."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.core.sync import sync_service
from app.models.finding import Finding
from app.models.project import Project


def _ev(eid, ts, pid, title, occ):
    return {
        "event_id": eid, "entity_type": "findings", "entity_public_id": pid,
        "operation": "create",
        "payload": {"public_id": pid, "project_public_id": "proj-1",
                    "title": title, "occurrences": occ},
        "logical_ts": ts, "source_node_id": "node-a",
    }


async def _run():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        db.add(Project(public_id="proj-1", name="P"))
        await db.commit()
        # Two nodes independently found the same thing (different public_ids).
        await sync_service.ingest_remote_events(db, peer_node_id="p", events=[
            _ev("e1", 10, "find-aaa", "SMB signing disabled", 3),
            _ev("e2", 11, "find-bbb", "SMB signing disabled", 5),
        ])
        await db.commit()
        n = (await db.execute(select(func.count()).select_from(Finding))).scalar()
        assert n == 1, f"duplicates must converge to one row, got {n}"
        occ = (await db.execute(select(Finding.occurrences))).scalar()
        assert occ == 5, f"occurrences must be max, got {occ}"
        # Stale replay with lower count must not regress (fresh event_id, newer ts).
        await sync_service.ingest_remote_events(db, peer_node_id="p", events=[
            _ev("e3", 12, "find-aaa", "SMB signing disabled", 1),
        ])
        await db.commit()
        occ2 = (await db.execute(select(Finding.occurrences))).scalar()
        assert occ2 == 5, f"replay regressed occurrences to {occ2}"
    await engine.dispose()
    print("PASS finding converge")


def test_finding_dedup_converges_and_maxes():
    asyncio.run(_run())


if __name__ == "__main__":
    test_finding_dedup_converges_and_maxes()
