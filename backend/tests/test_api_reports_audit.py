"""Audit 42: report list/download scoped by project membership; deleted hosts excluded."""
import asyncio
import tempfile
from pathlib import Path

from tests.api_audit_fixture import api_env
from app.config import settings
from app.core.utils import utcnow
from app.models.host import Host
from app.models.project import Project


def test_reports_list_download_scoped_and_no_deleted_hosts():
    async def run():
        old = settings.REPORTS_PATH
        with tempfile.TemporaryDirectory() as tmp:
            settings.REPORTS_PATH = Path(tmp)
            try:
                async with api_env() as env:
                    async with env.maker() as db:
                        p1, p2 = Project(name="One"), Project(name="Two")
                        db.add_all([p1, p2]); await db.flush()
                        db.add_all([
                            Host(project_id=p1.id, ip_address="10.0.0.1"),
                            Host(project_id=p1.id, ip_address="10.6.6.6", deleted_at=utcnow()),
                        ])
                        await db.commit()
                        pid1, pid2 = p1.id, p2.id
                    await env.member_of(pid1)
                    r = await env.cli.post("/api/v1/reports/generate", headers=env.op,
                                           params={"project_id": pid1, "format": "markdown"})
                    assert r.status_code == 200, r.text
                    assert "10.0.0.1" in r.text and "10.6.6.6" not in r.text
                    mine = r.headers["content-disposition"].split("filename=")[1].strip('"')
                    other = f"report_p{pid2}_Two_20200101_000000.md"
                    (Path(tmp) / other).write_text("secret of project two")
                    (Path(tmp) / "report_legacy_20200101_000000.md").write_text("legacy")

                    r = await env.cli.get("/api/v1/reports/list", headers=env.op)
                    names = {x["name"] for x in r.json()["reports"]}
                    assert names == {mine}, names
                    r = await env.cli.get("/api/v1/reports/list", headers=env.out)
                    assert r.json()["reports"] == []

                    r = await env.cli.get(f"/api/v1/reports/download/{mine}", headers=env.op)
                    assert r.status_code == 200
                    for name in (other, "report_legacy_20200101_000000.md"):
                        r = await env.cli.get(f"/api/v1/reports/download/{name}", headers=env.op)
                        assert r.status_code == 403, (name, r.status_code)
                    r = await env.cli.get(f"/api/v1/reports/download/{mine}", headers=env.out)
                    assert r.status_code == 403
            finally:
                settings.REPORTS_PATH = old
    asyncio.run(run())
