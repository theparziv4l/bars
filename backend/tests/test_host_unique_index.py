"""Live hosts are unique per (project, ip); migration dedupes existing rows."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import text, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base, _apply_schema_migrations
import app.models  # noqa: F401
from app.models.host import Host, Service
from app.models.project import Project


async def _fresh(drop_index=False):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if drop_index:
            await conn.execute(text("DROP INDEX ux_hosts_project_ip_live"))
    return engine


async def _unique_enforced():
    engine = await _fresh()
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        p = Project(name="P"); db.add(p); await db.flush()
        db.add(Host(project_id=p.id, ip_address="10.0.0.1")); await db.commit()
        # soft-deleted and NULL-ip duplicates are allowed
        db.add(Host(project_id=p.id, ip_address="10.0.0.1", deleted_at=text("CURRENT_TIMESTAMP")))
        db.add_all([Host(project_id=p.id, ip_address=None), Host(project_id=p.id, ip_address=None)])
        await db.commit()
        db.add(Host(project_id=p.id, ip_address="10.0.0.1"))
        with pytest.raises(IntegrityError):
            await db.commit()
    await engine.dispose()


async def _migration_dedupes():
    engine = await _fresh(drop_index=True)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        p = Project(name="P"); db.add(p); await db.flush()
        h1 = Host(project_id=p.id, ip_address="10.0.0.1")
        h2 = Host(project_id=p.id, ip_address="10.0.0.1")
        h3 = Host(project_id=p.id, ip_address="10.0.0.1")
        other = Host(project_id=p.id, ip_address="10.0.0.2")
        db.add_all([h1, h2, h3, other]); await db.flush()
        db.add_all([Service(host_id=h2.id, port=22), Service(host_id=h3.id, port=80)])
        keep_id, dup2, dup3, other_id = h1.id, h2.id, h3.id, other.id
        for hid, key in ((keep_id, "a"), (dup2, "a"), (dup2, "b"), (dup3, "b")):
            await db.execute(text(
                "INSERT INTO project_variables (public_id, project_id, key, value, var_type, host_id,"
                " created_at, updated_at) VALUES (:pid, :p, :k, 'v', 'string', :h,"
                " CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"),
                {"pid": f"v-{hid}-{key}", "p": p.id, "k": key, "h": hid})
        await db.commit()

    async with engine.begin() as conn:
        await conn.run_sync(_apply_schema_migrations)
        await conn.run_sync(_apply_schema_migrations)  # idempotent

    async with Session() as db:
        live = (await db.execute(
            select(Host.id).where(Host.deleted_at.is_(None), Host.ip_address == "10.0.0.1")
        )).scalars().all()
        assert live == [keep_id], live
        assert (await db.execute(select(Host.deleted_at).where(Host.id == other_id))).scalar() is None
        svc = (await db.execute(select(Service.host_id))).scalars().all()
        assert sorted(svc) == [keep_id, keep_id], svc
        keys = (await db.execute(text(
            "SELECT key FROM project_variables WHERE host_id=:h AND deleted_at IS NULL ORDER BY key"),
            {"h": keep_id})).scalars().all()
        assert keys == ["a", "b"], keys
        idx = (await db.execute(text(
            "SELECT 1 FROM sqlite_master WHERE name='ux_hosts_project_ip_live'"))).scalar()
        assert idx == 1
    await engine.dispose()


def test_unique_enforced():
    asyncio.run(_unique_enforced())


def test_migration_dedupes_existing_duplicates():
    asyncio.run(_migration_dedupes())
