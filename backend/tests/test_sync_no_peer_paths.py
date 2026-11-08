"""Peer payloads can never set DiscoveredFile.local_path (arbitrary file read)."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.core.sync import SyncService
from app.models.file import DiscoveredFile
from app.models.host import Host
from app.models.project import Project


async def _run():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    svc = SyncService()
    async with maker() as db:
        proj = Project(public_id="p1", name="P"); db.add(proj); await db.flush()
        db.add(Host(public_id="h1", project_id=proj.id, ip_address="10.0.0.1"))
        await db.flush()
        base = {"public_id": "f1", "host_public_id": "h1", "share_name": "s",
                "path": "/", "name": "n", "local_path": "/etc/passwd",
                "extra_data": {"local_path": "/etc/shadow"}}
        await svc._apply_event(db, entity_type="discovered_files", operation="create", payload=base)
        await db.flush()
        f = (await db.execute(select(DiscoveredFile))).scalar_one()
        assert f.local_path is None, f.local_path
        # Local download sets it; a later peer update must not replace it.
        f.local_path = "/home/me/project/files/n"
        await db.flush()
        await svc._apply_event(db, entity_type="discovered_files", operation="update",
                               payload={**base, "local_path": "/etc/passwd"})
        await db.flush()
        await db.refresh(f)
        assert f.local_path == "/home/me/project/files/n", f.local_path
    await engine.dispose()


def test_peer_cannot_set_discovered_file_local_path():
    asyncio.run(_run())
