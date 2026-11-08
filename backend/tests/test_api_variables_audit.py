"""Audit 40: variable upserts must hit exactly the global/domain, live row."""
import asyncio

from sqlalchemy import select

from tests.api_audit_fixture import api_env
from app.core.utils import utcnow
from app.models.host import Host
from app.models.project import Project
from app.models.library_variable import LibraryVariable
from app.models.variable import ProjectVariable


async def _seed(env):
    async with env.maker() as db:
        p = Project(name="A"); db.add(p); await db.flush()
        h = Host(project_id=p.id, ip_address="10.0.0.1"); db.add(h); await db.flush()
        db.add_all([
            ProjectVariable(project_id=p.id, key="k", value="host", host_id=h.id),
            ProjectVariable(project_id=p.id, key="k", value="old", deleted_at=utcnow()),
        ])
        db.add(LibraryVariable(key="k", value="lib", var_type="string"))
        await db.commit()
        lib = (await db.execute(select(LibraryVariable))).scalar_one()
        pid, lid = p.id, lib.id
    await env.member_of(pid)
    return pid, lid


async def _live(env, pid):
    async with env.maker() as db:
        rows = (await db.execute(select(ProjectVariable).where(
            ProjectVariable.project_id == pid, ProjectVariable.deleted_at.is_(None)))).scalars().all()
        return {(r.host_id is not None): r.value for r in rows}, len(rows)


def test_project_variable_post_scoped():
    async def run():
        async with api_env() as env:
            pid, _ = await _seed(env)
            r = await env.cli.post(f"/api/v1/projects/{pid}/variables", headers=env.op,
                                   json={"key": "k", "value": "new"})
            assert r.status_code == 200, r.text
            vals, n = await _live(env, pid)
            assert n == 2 and vals == {True: "host", False: "new"}
            # second upsert updates the same global row
            r = await env.cli.post(f"/api/v1/projects/{pid}/variables", headers=env.op,
                                   json={"key": "k", "value": "newer"})
            assert r.status_code == 200, r.text
            vals, n = await _live(env, pid)
            assert n == 2 and vals == {True: "host", False: "newer"}
    asyncio.run(run())


def test_library_import_scoped():
    async def run():
        async with api_env() as env:
            pid, lid = await _seed(env)
            r = await env.cli.post(f"/api/v1/variables/import", headers=env.op,
                                   params={"project_id": pid}, json=[lid])
            assert r.status_code == 201, r.text
            vals, n = await _live(env, pid)
            assert n == 2 and vals == {True: "host", False: "lib"}
    asyncio.run(run())
