"""Duplicate live IP in a project: 409 on create/update, never a 500."""
import asyncio

from sqlalchemy import select, func

from tests.api_audit_fixture import api_env
from app.models.host import Host
from app.models.project import Project


def _env_run(body):
    async def run():
        async with api_env() as env:
            async with env.maker() as db:
                p = Project(name="P"); db.add(p); await db.flush()
                h1 = Host(project_id=p.id, ip_address="10.0.0.1")
                h2 = Host(project_id=p.id, ip_address="10.0.0.2")
                db.add_all([h1, h2]); await db.commit()
                pid, id1, id2 = p.id, h1.id, h2.id
            await env.member_of(pid)
            await body(env, pid, id1, id2)
    asyncio.run(run())


def test_create_duplicate_ip_409():
    async def body(env, pid, id1, id2):
        r = await env.cli.post("/api/v1/hosts", headers=env.op,
                               json={"project_id": pid, "ip_address": "10.0.0.1"})
        assert r.status_code == 409, r.text
        assert "10.0.0.1" in r.text
        r = await env.cli.post("/api/v1/hosts", headers=env.op,
                               json={"project_id": pid, "ip_address": "10.0.0.9"})
        assert r.status_code == 201, r.text
        async with env.maker() as db:
            n = (await db.execute(select(func.count()).select_from(Host)
                                  .where(Host.ip_address == "10.0.0.1"))).scalar()
            assert n == 1
    _env_run(body)


def test_update_to_duplicate_ip_409():
    async def body(env, pid, id1, id2):
        r = await env.cli.put(f"/api/v1/hosts/{id2}", headers=env.op,
                              json={"ip_address": "10.0.0.1"})
        assert r.status_code == 409, r.text
        r = await env.cli.put(f"/api/v1/hosts/{id2}", headers=env.op,
                              json={"ip_address": "10.0.0.2", "notes": "same ip ok"})
        assert r.status_code == 200, r.text
    _env_run(body)
