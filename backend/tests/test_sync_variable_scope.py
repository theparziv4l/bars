"""Project variable applier matches by exact scope; payload carries the ad_domain public id."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.core.sync import SyncService, project_variable_sync_payload
from app.models.ad_domain import ADDomain
from app.models.host import Host
from app.models.project import Project
from app.models.variable import ProjectVariable


async def _run():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    svc = SyncService()
    async with maker() as db:
        proj = Project(public_id="p1", name="P"); db.add(proj); await db.flush()
        host = Host(public_id="h1", project_id=proj.id, ip_address="10.0.0.1")
        dom = ADDomain(public_id="d1", project_id=proj.id, name="corp.local")
        db.add_all([host, dom]); await db.flush()
        g = ProjectVariable(public_id="v-global", project_id=proj.id, key="K", value="g")
        h = ProjectVariable(public_id="v-host", project_id=proj.id, key="K", value="h", host_id=host.id)
        d = ProjectVariable(public_id="v-dom", project_id=proj.id, key="K", value="d", ad_domain_id=dom.id)
        db.add_all([g, h, d]); await db.flush()

        # Payload builder carries the domain public id.
        p = await project_variable_sync_payload(db, d)
        assert p["ad_domain_public_id"] == "d1", p
        assert (await project_variable_sync_payload(db, g))["ad_domain_public_id"] is None

        # Same key in three scopes: adoption must not raise nor cross scopes.
        base = {"project_public_id": "p1", "key": "K", "var_type": "string"}
        await svc._apply_project_variable(db, payload={
            **base, "public_id": "n-host", "host_public_id": "h1", "value": "H2"})
        await svc._apply_project_variable(db, payload={
            **base, "public_id": "n-global", "host_public_id": None, "value": "G2"})
        await svc._apply_project_variable(db, payload={
            **base, "public_id": "n-dom", "ad_domain_public_id": "d1", "value": "D2"})
        await db.flush()
        rows = {r.public_id: r for r in (await db.execute(select(ProjectVariable))).scalars().all()}
        assert set(rows) == {"n-host", "n-global", "n-dom"}, set(rows)  # adopted, no 4th/5th row
        assert rows["n-host"].value == "H2" and rows["n-host"].host_id == host.id
        assert rows["n-global"].value == "G2" and rows["n-global"].host_id is None
        assert rows["n-dom"].value == "D2" and rows["n-dom"].ad_domain_id == dom.id

        # A host-scoped var whose host has not arrived must defer, not land global.
        from app.core.sync import _FKNotReady
        try:
            await svc._apply_project_variable(db, payload={
                **base, "public_id": "orphan", "host_public_id": "ghost", "value": "x"})
            raise AssertionError("expected _FKNotReady")
        except _FKNotReady:
            pass
    await engine.dispose()


def test_variable_applier_exact_scope():
    asyncio.run(_run())
