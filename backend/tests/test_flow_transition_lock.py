"""Pause racing a completion event leaves a consistent flow; locks evicted."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.api.flows import pause_flow_execution
from app.core.flow_manager import FlowManager
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.execution import Execution, ExecutionStatus
from app.models.flow import Flow, FlowStep
from app.models.flow_execution import FlowExecution
from app.models.project import Project


async def _seed(maker, n_steps=2):
    async with maker() as db:
        proj = Project(name="P"); db.add(proj); await db.flush()
        grp = ChecklistGroup(project_id=proj.id, name="G"); db.add(grp); await db.flush()
        items = [ChecklistItem(group_id=grp.id, name=f"s{i}", command_template="true")
                 for i in range(n_steps)]
        db.add_all(items); await db.flush()
        flow = Flow(project_id=proj.id, name="F"); db.add(flow); await db.flush()
        db.add_all([FlowStep(flow_id=flow.id, checklist_item_id=items[i].id,
                             order_index=i) for i in range(n_steps)])
        db.add(FlowExecution(id="fx-p", flow_id=flow.id, status="running"))
        db.add(Execution(public_id="ex-s0", item_id=items[0].id, command="true",
                         status=ExecutionStatus.COMPLETED, flow_execution_id="fx-p",
                         flow_step_index=0))
        await db.commit()
        return flow.id


async def _run_race():
    from unittest.mock import patch
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    flow_id = await _seed(maker)
    fm = FlowManager()
    import app.core.flow_manager as fm_mod
    with patch.object(fm_mod, "flow_manager", fm):
        async def do_pause():
            async with maker() as db:
                try:
                    await pause_flow_execution(flow_id, "fx-p", db, None)
                except Exception:
                    pass

        async def do_advance():
            async with maker() as db:
                ex = (await db.execute(select(Execution).where(
                    Execution.public_id == "ex-s0"))).scalar_one()
                await fm._schedule_next_steps(db, "fx-p", ex)

        await asyncio.gather(do_pause(), do_advance())
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    async with maker() as db:
        st = (await db.execute(select(FlowExecution.status).where(
            FlowExecution.id == "fx-p"))).scalar()
        n1 = (await db.execute(select(func.count()).select_from(Execution).where(
            Execution.flow_execution_id == "fx-p",
            Execution.flow_step_index == 1,
            Execution.status != ExecutionStatus.CANCELLED))).scalar()
    # Consistent outcomes only: paused with nothing live scheduled, or running
    # with stage 1 scheduled. Never: paused AND live stage-1 rows (advance
    # won after pause), which strands rows no one resumes.
    assert not (st == "paused" and n1 > 0), f"paused but scheduled: {st}/{n1}"
    await engine.dispose()
    print("PASS transition race")


async def _run_evict():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    await _seed(maker, n_steps=1)
    fm = FlowManager()
    async with maker() as db:
        ex = (await db.execute(select(Execution).where(
            Execution.public_id == "ex-s0"))).scalar_one()
        await fm._schedule_next_steps(db, "fx-p", ex)
        await db.commit()
    assert "fx-p" not in fm._flow_locks, f"lock leaked: {list(fm._flow_locks)}"
    await engine.dispose()
    print("PASS lock evict")


def test_pause_race_consistent():
    asyncio.run(_run_race())


def test_finalize_evicts_lock():
    asyncio.run(_run_evict())


if __name__ == "__main__":
    test_pause_race_consistent()
    test_finalize_evicts_lock()
