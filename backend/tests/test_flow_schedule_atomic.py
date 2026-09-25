"""A stage that fails to schedule fails the flow (no half-commit, no skip)."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.core.flow_manager import flow_manager
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.execution import Execution, ExecutionStatus
from app.models.flow import Flow, FlowStep
from app.models.flow_execution import FlowExecution
from app.models.project import Project


async def _seed(maker):
    async with maker() as db:
        proj = Project(name="P"); db.add(proj); await db.flush()
        grp = ChecklistGroup(project_id=proj.id, name="G"); db.add(grp); await db.flush()
        i0 = ChecklistItem(group_id=grp.id, name="s0", command_template="true")
        i1 = ChecklistItem(group_id=grp.id, name="s1", command_template="true")
        i2 = ChecklistItem(group_id=grp.id, name="s2", command_template="true")
        db.add_all([i0, i1, i2]); await db.flush()
        flow = Flow(project_id=proj.id, name="F"); db.add(flow); await db.flush()
        db.add_all([
            FlowStep(flow_id=flow.id, checklist_item_id=i0.id, order_index=0),
            FlowStep(flow_id=flow.id, checklist_item_id=i1.id, order_index=1),
            FlowStep(flow_id=flow.id, checklist_item_id=i2.id, order_index=1),
        ])
        db.add(FlowExecution(id="fx-a", flow_id=flow.id, status="running"))
        db.add(Execution(public_id="ex-s0", item_id=i0.id, command="true",
                         status=ExecutionStatus.COMPLETED, flow_execution_id="fx-a",
                         flow_step_index=0))
        await db.commit()


async def _run():
    from unittest.mock import patch
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    await _seed(maker)

    from app.core import orchestrator as orch_mod
    orig = orch_mod.task_orchestrator.create_execution_record
    calls = []

    async def flaky_create(*a, **k):
        calls.append(k.get("item_id"))
        if len(calls) == 2:
            raise ValueError("schedule boom")
        return await orig(*a, **k)

    with patch.object(orch_mod.task_orchestrator, "create_execution_record", flaky_create):
        async with maker() as db:
            ex = (await db.execute(
                select(Execution).where(Execution.public_id == "ex-s0"))).scalar_one()
            await flow_manager._schedule_next_steps(db, "fx-a", ex)
            await db.commit()
    async with maker() as db:
        n1 = (await db.execute(select(func.count()).select_from(Execution).where(
            Execution.flow_execution_id == "fx-a",
            Execution.flow_step_index == 1))).scalar()
        st = (await db.execute(
            select(FlowExecution.status).where(FlowExecution.id == "fx-a"))).scalar()
        assert n1 == 0, f"half-committed stage: {n1} rows at step 1"
        assert st == "failed", f"flow must fail loudly, got {st}"
    await engine.dispose()
    print("PASS schedule atomic")


def test_schedule_failure_fails_flow():
    asyncio.run(_run())


if __name__ == "__main__":
    test_schedule_failure_fails_flow()
