"""Audit 43: start_execution must not accept a host from another project / deleted."""
import asyncio
from unittest.mock import patch, AsyncMock

from tests.api_audit_fixture import api_env
from app.core.orchestrator import task_orchestrator
from app.core.utils import utcnow
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.host import Host
from app.models.project import Project


def test_start_execution_host_scope():
    async def run():
        async with api_env() as env:
            async with env.maker() as db:
                p1, p2 = Project(name="One"), Project(name="Two")
                db.add_all([p1, p2]); await db.flush()
                g = ChecklistGroup(project_id=p1.id, name="G"); db.add(g); await db.flush()
                it = ChecklistItem(group_id=g.id, name="s0", command_template="true")
                h_ok = Host(project_id=p1.id, ip_address="10.0.0.1")
                h_other = Host(project_id=p2.id, ip_address="10.9.0.1")
                h_del = Host(project_id=p1.id, ip_address="10.0.0.2", deleted_at=utcnow())
                db.add_all([it, h_ok, h_other, h_del]); await db.commit()
                pid1, pid2 = p1.id, p2.id
                item_id, ok, other, dele = it.id, h_ok.id, h_other.id, h_del.id
            await env.member_of(pid1, pid2)  # member of both: only scope is at fault
            with patch.object(task_orchestrator, "run_execution_background", AsyncMock()):
                for hid in (other, dele):
                    r = await env.cli.post("/api/v1/executions", headers=env.op,
                                           json={"item_id": item_id, "host_id": hid})
                    assert r.status_code in (400, 404), (hid, r.status_code, r.text)
                r = await env.cli.post("/api/v1/executions", headers=env.op,
                                       json={"item_id": item_id, "host_id": ok})
                assert r.status_code == 201, r.text
    asyncio.run(run())
