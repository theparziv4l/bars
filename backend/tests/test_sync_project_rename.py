"""Renaming a project via sync must keep its existing on-disk path."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.core.sync import sync_service
from app.core.utils import get_project_path


async def _run():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        await sync_service._apply_project(db, payload={"public_id": "p1", "name": "Old"})
        await db.flush()
        await sync_service._apply_project(db, payload={
            "public_id": "p1", "name": "New",
            "extra_data": {"project_path": "/peer/elsewhere/New"},
        })
        await db.flush()
        from sqlalchemy import select
        from app.models.project import Project
        p = (await db.execute(select(Project).where(Project.public_id == "p1"))).scalar_one()
        assert p.name == "New"
        assert p.extra_data["project_path"] == get_project_path("Old"), p.extra_data
    await engine.dispose()


def test_rename_keeps_local_path():
    asyncio.run(_run())


if __name__ == "__main__":
    test_rename_keeps_local_path()
    print("PASS")
