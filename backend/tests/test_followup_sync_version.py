"""_sync_execution_to_project_item must number the clone after existing project-item runs."""
import asyncio

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from discovery_fixture import make_engine, session
from app.core.orchestrator import task_orchestrator
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.execution import Execution, ExecutionStatus
from app.models.project import Project


def test_sync_clone_uses_next_version(tmp_path):
    async def go():
        engine = await make_engine(tmp_path)
        async with session(engine) as db:
            proj = Project(name="P")
            tgrp = ChecklistGroup(project_id=None, name="G")
            db.add_all([proj, tgrp]); await db.flush()
            pgrp = ChecklistGroup(project_id=proj.id, name="G"); db.add(pgrp); await db.flush()
            titem = ChecklistItem(group_id=tgrp.id, name="I", command_template="echo hi")
            pitem = ChecklistItem(group_id=pgrp.id, name="I", command_template="echo hi")
            db.add_all([titem, pitem]); await db.flush()
            db.add(Execution(public_id="old", item_id=pitem.id, command="x", version=4,
                             status=ExecutionStatus.COMPLETED))
            ex = Execution(public_id="ex-v", item_id=titem.id, command="echo hi", stdout="h",
                           status=ExecutionStatus.COMPLETED,
                           variables_used={"project_id": proj.id})
            db.add(ex); await db.commit()
            item = (await db.execute(
                select(ChecklistItem).where(ChecklistItem.id == titem.id)
                .options(selectinload(ChecklistItem.group)))).scalar_one()
            await task_orchestrator._sync_execution_to_project_item(db, ex, item)
            versions = sorted(v for v in (await db.execute(
                select(Execution.version).where(Execution.item_id == pitem.id))).scalars())
            assert versions == [4, 5], versions
        await engine.dispose()
    asyncio.run(go())
