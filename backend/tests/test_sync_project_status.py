"""Unknown incoming project status keeps the existing local status."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.core.sync import sync_service
from app.models.project import Project, ProjectStatus


async def _run():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        db.add(Project(public_id="p1", name="P", status=ProjectStatus.ACTIVE))
        await db.commit()
        await sync_service.ingest_remote_events(db, peer_node_id="peer", events=[{
            "event_id": "st1", "entity_type": "projects", "entity_public_id": "p1",
            "operation": "update",
            "payload": {"public_id": "p1", "status": "frozen-by-newer-peer"},
            "logical_ts": 10, "source_node_id": "n"}])
        await db.commit()
        st = (await db.execute(select(Project.status).where(Project.public_id == "p1"))).scalar()
        assert st == ProjectStatus.ACTIVE, f"unknown status must keep existing, got {st}"
    await engine.dispose()
    print("PASS status keep")


def test_unknown_status_keeps_existing():
    asyncio.run(_run())


if __name__ == "__main__":
    test_unknown_status_keeps_existing()
