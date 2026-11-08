"""Audit 61: checklist execution stats skip deleted rows; null on NOT NULL ignored."""
import asyncio

from tests.api_audit_fixture import api_env
from app.core.utils import utcnow
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.execution import Execution, ExecutionStatus
from app.models.project import Project


def test_item_update_null_and_stats():
    async def run():
        async with api_env() as env:
            async with env.maker() as db:
                p = Project(name="P"); db.add(p); await db.flush()
                g = ChecklistGroup(project_id=p.id, name="G"); db.add(g); await db.flush()
                it = ChecklistItem(group_id=g.id, name="i", command_template="true")
                db.add(it); await db.flush()
                db.add_all([
                    Execution(public_id="e1", item_id=it.id, command="true", version=1,
                              status=ExecutionStatus.COMPLETED, exit_code=0),
                    Execution(public_id="e2", item_id=it.id, command="true", version=2,
                              status=ExecutionStatus.FAILED, exit_code=1, deleted_at=utcnow()),
                    Execution(public_id="e3", item_id=it.id, command="true", version=3,
                              status=ExecutionStatus.COMPLETED, exit_code=0, deleted_at=utcnow()),
                ])
                await db.commit()
                pid, iid, gid = p.id, it.id, g.id
            await env.member_of(pid)
            r = await env.cli.put(f"/api/v1/checklists/items/{iid}", headers=env.op,
                                  json={"name": None, "command_template": None, "timeout": None,
                                        "enabled": None, "tags": None, "description": "d"})
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["name"] == "i" and body["description"] == "d"
            assert body["execution_count"] == 1, body
            r = await env.cli.put(f"/api/v1/checklists/groups/{gid}", headers=env.op,
                                  json={"name": None, "collapsed": None, "speed_profile": None})
            assert r.status_code == 200, r.text
            assert r.json()["name"] == "G"
    asyncio.run(run())
