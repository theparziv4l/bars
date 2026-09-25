"""Late completion events never resurrect a terminal flow."""
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
        db.add_all([i0, i1]); await db.flush()
        flow = Flow(project_id=proj.id, name="F"); db.add(flow); await db.flush()
        db.add_all([
            FlowStep(flow_id=flow.id, checklist_item_id=i0.id, order_index=0),
            FlowStep(flow_id=flow.id, checklist_item_id=i1.id, order_index=1),
        ])
        fe = FlowExecution(id="fx-1", flow_id=flow.id, status="cancelled")
        db.add(fe)
        ex = Execution(public_id="ex-late", item_id=i0.id, command="true",
                       status=ExecutionStatus.COMPLETED, flow_execution_id="fx-1",
                       flow_step_index=0)
        db.add(ex); await db.commit()
        return ex.id


async def _run():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    ex_id = await _seed(maker)
    async with maker() as db:
        ex = (await db.execute(select(Execution).where(Execution.id == ex_id))).scalar_one()
        await flow_manager._schedule_next_steps(db, "fx-1", ex)
        await db.commit()
        n = (await db.execute(select(func.count()).select_from(Execution))).scalar()
        assert n == 1, f"late event must not schedule; executions={n}"
        st = (await db.execute(select(FlowExecution.status).where(FlowExecution.id == "fx-1"))).scalar()
        assert st == "cancelled", f"status must stay cancelled, got {st}"
    await engine.dispose()
    print("PASS terminal guard")


def test_late_event_no_resurrection():
    asyncio.run(_run())


if __name__ == "__main__":
    test_late_event_no_resurrection()
