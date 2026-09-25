"""A resumed stage completing does not self-cancel on paused (superseded) rows."""
import asyncio
import os
import sys
from datetime import datetime, timedelta

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


async def _seed(maker):
    async with maker() as db:
        proj = Project(name="P"); db.add(proj); await db.flush()
        grp = ChecklistGroup(project_id=proj.id, name="G"); db.add(grp); await db.flush()
        i0 = ChecklistItem(group_id=grp.id, name="s0", command_template="true")
        db.add(i0); await db.flush()
        flow = Flow(project_id=proj.id, name="F"); db.add(flow); await db.flush()
        db.add(FlowStep(flow_id=flow.id, checklist_item_id=i0.id, order_index=0))
        db.add(FlowExecution(id="fx-r", flow_id=flow.id, status="running"))
        now = datetime.utcnow()
        # Gen 1: paused (cancelled) at t0. Gen 2: resumed row completed at t0+10.
        db.add(Execution(public_id="ex-old", item_id=i0.id, command="true",
                         status=ExecutionStatus.CANCELLED, flow_execution_id="fx-r",
                         flow_step_index=0, created_at=now,
                         completed_at=now + timedelta(seconds=1)))
        db.add(Execution(public_id="ex-new", item_id=i0.id, command="true",
                         status=ExecutionStatus.COMPLETED, flow_execution_id="fx-r",
                         flow_step_index=0, created_at=now + timedelta(seconds=5),
                         completed_at=now + timedelta(seconds=10)))
        await db.commit()


async def _run():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    await _seed(maker)
    async with maker() as db:
        ex = (await db.execute(
            select(Execution).where(Execution.public_id == "ex-new"))).scalar_one()
        await flow_manager._schedule_next_steps(db, "fx-r", ex)
        await db.commit()
        st = (await db.execute(
            select(FlowExecution.status).where(FlowExecution.id == "fx-r"))).scalar()
        # Single-stage flow: completion must finalize COMPLETED, not self-cancel.
        assert st == "completed", f"resumed flow self-cancelled: {st}"
    await engine.dispose()
    print("PASS resume generations")


def test_resumed_stage_completion_finalizes():
    asyncio.run(_run())


if __name__ == "__main__":
    test_resumed_stage_completion_finalizes()
