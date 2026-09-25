"""Flow start cancels only same-flow stale runs, and notifies each."""
import asyncio
import os
import sys
from unittest.mock import patch, AsyncMock

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
        item = ChecklistItem(group_id=grp.id, name="s0", command_template="true")
        db.add(item); await db.flush()
        f1 = Flow(project_id=proj.id, name="F1"); f2 = Flow(project_id=proj.id, name="F2")
        db.add_all([f1, f2]); await db.flush()
        db.add(FlowStep(flow_id=f1.id, checklist_item_id=item.id, order_index=0))
        db.add(FlowExecution(id="fx-old", flow_id=f1.id, status="running"))
        db.add(FlowExecution(id="fx-other", flow_id=f2.id, status="running"))
        await db.flush()
        # Stale rows: same flow's prior run, other flow's run, manual (no run).
        db.add(Execution(public_id="ex-same", item_id=item.id, command="true",
                         status=ExecutionStatus.RUNNING, flow_execution_id="fx-old",
                         flow_step_index=0))
        db.add(Execution(public_id="ex-other", item_id=item.id, command="true",
                         status=ExecutionStatus.RUNNING, flow_execution_id="fx-other",
                         flow_step_index=0))
        db.add(Execution(public_id="ex-man", item_id=item.id, command="true",
                         status=ExecutionStatus.RUNNING))
        await db.commit()
        return f1.id


async def _run():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    flow_id = await _seed(maker)
    notified = []
    created = Execution(id=900, public_id="ex-new", item_id=1, command="true",
                        status=ExecutionStatus.PENDING)
    with patch("app.core.flow_manager.task_orchestrator") as orch, \
         patch("app.core.notifications.notification_manager") as nm:
        orch.create_execution_record = AsyncMock(return_value=created)
        orch.run_execution_background = AsyncMock(return_value=None)
        nm.send_execution_status = AsyncMock(
            side_effect=lambda eid, st, **k: notified.append((eid, st)))
        async with maker() as db:
            await flow_manager.start_flow(db, flow_id=flow_id)
        # Drain the fire-and-forget background task spawned by start_flow.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    async with maker() as db:
        rows = {e.public_id: e.status for e in
                (await db.execute(select(Execution))).scalars().all()}
    assert rows["ex-same"] == ExecutionStatus.CANCELLED, rows
    assert rows["ex-other"] == ExecutionStatus.RUNNING, rows
    assert rows["ex-man"] == ExecutionStatus.RUNNING, rows
    assert any(s == "cancelled" for _, s in notified), f"notified={notified}"
    await engine.dispose()
    print("PASS stale cancel")


def test_stale_cancel_scoped_and_notified():
    asyncio.run(_run())


if __name__ == "__main__":
    test_stale_cancel_scoped_and_notified()
