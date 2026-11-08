"""Audit 7/44/49/61 (projects API)."""
import asyncio
import tempfile
from unittest.mock import patch

from sqlalchemy import select

from tests.api_audit_fixture import api_env
from app.core.host_runner import host_runner
from app.core.utils import utcnow
from app.models.host import Host, Service
from app.models.project import Project


def test_create_project_commits_before_agent_call():
    seen = {}

    async def run():
        with tempfile.TemporaryDirectory() as tmp:
            async with api_env(f"sqlite+aiosqlite:///{tmp}/t.db") as env:
                async def fake(cmd, *a, **k):
                    if cmd == "ip -j addr":
                        # A second connection only sees committed rows.
                        async with env.maker() as other:
                            seen["rows"] = len((await other.execute(select(Project))).scalars().all())
                    return {"exit_code": 1, "stdout": "", "stderr": ""}
                with patch.object(host_runner, "execute_sync", fake):
                    r = await env.cli.post("/api/v1/projects", headers=env.op, json={"name": "NewP"})
                assert r.status_code == 201, r.text
    asyncio.run(run())
    assert seen.get("rows") == 1, seen


def test_update_project_merges_extra_data_and_ignores_null():
    async def run():
        async with api_env() as env:
            async with env.maker() as db:
                p = Project(name="P", extra_data={"project_path": "/x/P",
                                                  "smb_credentials": {"username": "u"}})
                db.add(p); await db.commit()
                pid = p.id
            await env.member_of(pid)
            r = await env.cli.put(f"/api/v1/projects/{pid}", headers=env.op,
                                  json={"extra_data": {"client": "ACME"}, "name": None,
                                        "scope": None, "status": None, "description": "d"})
            assert r.status_code == 200, r.text
            async with env.maker() as db:
                p = (await db.execute(select(Project))).scalar_one()
                assert p.extra_data == {"project_path": "/x/P",
                                        "smb_credentials": {"username": "u"}, "client": "ACME"}
                assert p.name == "P" and p.description == "d"
    asyncio.run(run())


def test_counts_tags_and_service_export_skip_deleted_hosts():
    async def run():
        async with api_env() as env:
            async with env.maker() as db:
                p = Project(name="P"); db.add(p); await db.flush()
                live = Host(project_id=p.id, ip_address="10.0.0.1", tags=["http"])
                dead = Host(project_id=p.id, ip_address="10.6.6.6", tags=["ghost"], deleted_at=utcnow())
                db.add_all([live, dead]); await db.flush()
                db.add_all([Service(host_id=live.id, port=80, name="http", state="open"),
                            Service(host_id=dead.id, port=8080, name="http", state="open")])
                await db.commit()
                pid = p.id
            await env.member_of(pid)
            r = await env.cli.get(f"/api/v1/projects/{pid}", headers=env.op)
            assert r.json()["host_count"] == 1, r.text
            r = await env.cli.get(f"/api/v1/projects/{pid}/host-tags", headers=env.op)
            assert r.json()["tags"] == ["http"], r.text
            written = []

            async def fake(cmd, *a, **k):
                written.append(cmd)
                return {"exit_code": 0, "stdout": "", "stderr": ""}
            with patch.object(host_runner, "execute_sync", fake):
                r = await env.cli.post(f"/api/v1/projects/{pid}/export-services", headers=env.op)
            assert r.status_code == 200, r.text
            joined = "\n".join(written)
            assert "10.0.0.1" in joined and "10.6.6.6" not in joined and "8080" not in joined
    asyncio.run(run())
