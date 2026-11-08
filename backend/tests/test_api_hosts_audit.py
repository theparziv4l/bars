"""Audit 38/61: smb_signing filter stays inside project+not-deleted; null update."""
import asyncio

from tests.api_audit_fixture import api_env
from app.models.host import Host
from app.models.project import Project
from app.core.utils import utcnow


def test_smb_signing_filter_scoped():
    async def run():
        async with api_env() as env:
            async with env.maker() as db:
                p1, p2 = Project(name="A"), Project(name="B")
                db.add_all([p1, p2]); await db.flush()
                db.add_all([
                    Host(project_id=p1.id, ip_address="10.0.0.1", extra_data={"smb_signing": True}),
                    Host(project_id=p1.id, ip_address="10.0.0.2", extra_data={"smb_signing": False}),
                    Host(project_id=p1.id, ip_address="10.0.0.3", extra_data={"smb_signing": "true"},
                         deleted_at=utcnow()),
                    Host(project_id=p2.id, ip_address="10.9.9.9", extra_data={"smb_signing": "true"}),
                    Host(project_id=p2.id, ip_address="10.9.9.8", extra_data={"smb_signing": "false"}),
                    Host(project_id=p2.id, ip_address="10.9.9.7", extra_data={}),
                ])
                await db.commit()
                pid = p1.id
            await env.member_of(pid)
            for val, expect in (("true", {"10.0.0.1"}), ("false", {"10.0.0.2"}),
                                ("unknown", set())):
                r = await env.cli.get("/api/v1/hosts", headers=env.op,
                                      params={"project_id": pid, "smb_signing": val})
                assert r.status_code == 200, r.text
                got = {h["ip_address"] for h in r.json()["items"]}
                assert got == expect, (val, got)
    asyncio.run(run())


def test_update_host_null_not_null_fields_ignored():
    async def run():
        async with api_env() as env:
            async with env.maker() as db:
                p = Project(name="A"); db.add(p); await db.flush()
                h = Host(project_id=p.id, ip_address="10.0.0.1", status="up")
                db.add(h); await db.commit()
                pid, hid = p.id, h.id
            await env.member_of(pid)
            r = await env.cli.put(f"/api/v1/hosts/{hid}", headers=env.op,
                                  json={"status": None, "extra_data": None,
                                        "tags": None, "excluded": None, "notes": "n"})
            assert r.status_code == 200, r.text
            assert r.json()["status"] == "up" and r.json()["notes"] == "n"
    asyncio.run(run())
