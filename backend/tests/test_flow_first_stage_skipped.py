"""A fully skipped FIRST stage advances to the next stage (not 'completed')."""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.core.flow_manager import flow_manager
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.execution import Execution, ExecutionStatus
from app.models.flow import Flow, FlowStep
from app.models.flow_execution import FlowExecution
from app.models.project import Project


async def _run(second_stage_runs: bool):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        proj = Project(name="P"); db.add(proj); await db.flush()
        grp = ChecklistGroup(project_id=proj.id, name="G"); db.add(grp); await db.flush()
        i0 = ChecklistItem(group_id=grp.id, name="s0", command_template="true")
        i1 = ChecklistItem(group_id=grp.id, name="s1", command_template="true")
        db.add_all([i0, i1]); await db.flush()
        flow = Flow(project_id=proj.id, name="F"); db.add(flow); await db.flush()
        db.add_all([
            FlowStep(flow_id=flow.id, checklist_item_id=i0.id, order_index=0, condition="1 == 2"),
            FlowStep(flow_id=flow.id, checklist_item_id=i1.id, order_index=1,
                     condition=None if second_stage_runs else "1 == 2"),
        ])
        await db.commit()
        flow_id = flow.id

    async def _fake_create(*a, **k):
        ex = Execution(public_id="ex-s", item_id=k["item_id"], command="true",
                       status=ExecutionStatus.PENDING,
                       flow_execution_id=k.get("flow_execution_id"),
                       flow_step_index=k.get("flow_step_index"))
        k["db"].add(ex)
        await k["db"].flush()
        return ex

    with patch("app.core.flow_manager.task_orchestrator") as orch:
        orch.create_execution_record = AsyncMock(side_effect=_fake_create)
        orch.run_execution_background = AsyncMock(return_value=None)
        async with maker() as db:
            fe, _ = await flow_manager.start_flow(db, flow_id, flow_execution_id="fx-first")
        await asyncio.sleep(0)
    async with maker() as db:
        rows = (await db.execute(select(Execution).where(
            Execution.flow_execution_id == "fx-first"))).scalars().all()
        st = (await db.execute(
            select(FlowExecution.status).where(FlowExecution.id == "fx-first"))).scalar()
    await engine.dispose()
    return rows, st


def test_first_stage_skipped_advances():
    rows, st = asyncio.run(_run(True))
    assert [e.flow_step_index for e in rows] == [1], rows
    assert st == "running", st


def test_all_stages_skipped_completes():
    rows, st = asyncio.run(_run(False))
    assert rows == []
    assert st == "completed", st
