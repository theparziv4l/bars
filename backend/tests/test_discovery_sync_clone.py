"""_sync_execution_to_project_item: no write txn held across cmd: extractor (agent HTTP)."""
import asyncio

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from discovery_fixture import make_engine, session
import app.core.orchestrator as orch_mod
from app.core.orchestrator import task_orchestrator
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.execution import Execution, ExecutionStatus
from app.models.project import Project


def test_sync_clone_commits_before_cmd_extractor(tmp_path, monkeypatch):
    async def go():
        engine = await make_engine(tmp_path)
        seen = {}
        async with session(engine) as db:
            proj = Project(name="P")
            tgrp = ChecklistGroup(project_id=None, name="G")
            db.add_all([proj, tgrp])
            await db.flush()
            pgrp = ChecklistGroup(project_id=proj.id, name="G")
            db.add(pgrp)
            await db.flush()
            titem = ChecklistItem(group_id=tgrp.id, name="I", command_template="echo hi")
            pitem = ChecklistItem(group_id=pgrp.id, name="I", command_template="echo hi",
                                  output_regex={"v": "cmd:echo x"})
            db.add_all([titem, pitem])
            await db.flush()
            ex = Execution(public_id="ex-sync", item_id=titem.id, command="echo hi",
                           stdout="hello", status=ExecutionStatus.COMPLETED,
                           variables_used={"project_id": proj.id})
            db.add(ex)
            await db.commit()

            async def fake_execute_sync(cmd, cwd=None, **kw):
                seen["in_txn"] = db.in_transaction()
                return {"exit_code": 0, "stdout": "x", "stderr": ""}

            monkeypatch.setattr(orch_mod.host_runner, "execute_sync", fake_execute_sync)
            item = (await db.execute(
                select(ChecklistItem).where(ChecklistItem.id == titem.id)
                .options(selectinload(ChecklistItem.group)))).scalar_one()
            await task_orchestrator._sync_execution_to_project_item(db, ex, item)

            assert "in_txn" in seen, "extractor never ran"
            assert seen["in_txn"] is False, "write transaction held across extractor call"
            clones = (await db.execute(
                select(Execution).where(Execution.item_id == pitem.id))).scalars().all()
            assert len(clones) == 1
        await engine.dispose()
    asyncio.run(go())
