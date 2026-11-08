"""Bulk execution maps orchestrator ValueErrors to 404 / 409 instead of 500."""
import asyncio
from unittest.mock import patch, AsyncMock

from tests.api_audit_fixture import api_env
from app.core.orchestrator import task_orchestrator
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.host import Host
from app.models.project import Project


def _bulk(message):
    async def run():
        async with api_env() as env:
            async with env.maker() as db:
                p = Project(name="P"); db.add(p); await db.flush()
                g = ChecklistGroup(project_id=p.id, name="G"); db.add(g); await db.flush()
                it = ChecklistItem(group_id=g.id, name="s", command_template="true")
                h = Host(project_id=p.id, ip_address="10.0.0.1")
                db.add_all([it, h]); await db.commit()
                pid, iid, hid = p.id, it.id, h.id
            await env.member_of(pid)
            with patch.object(task_orchestrator, "execute_bulk",
                              AsyncMock(side_effect=ValueError(message.format(iid)))):
                return await env.cli.post("/api/v1/executions/bulk", headers=env.op,
                                          json={"item_ids": [iid], "host_id": hid})
    return asyncio.run(run())


def test_bulk_not_found_is_404():
    r = _bulk("Checklist item {} not found")
    assert r.status_code == 404, r.text


def test_bulk_already_active_is_409():
    r = _bulk("Execution already active for item/host scope ({})")
    assert r.status_code == 409, r.text
    assert r.json()["detail"]["reason"] == "duplicate_running_execution"
