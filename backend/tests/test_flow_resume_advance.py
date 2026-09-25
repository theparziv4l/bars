"""Resume with an all-skipped stage advances instead of hanging running."""
import asyncio
import os
import sys

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


async def _seed(maker, paused: bool):
    async with maker() as db:
        proj = Project(name="P"); db.add(proj); await db.flush()
        grp = ChecklistGroup(project_id=proj.id, name="G"); db.add(grp); await db.flush()
        i0 = ChecklistItem(group_id=grp.id, name="s0", command_template="true")
        i1 = ChecklistItem(group_id=grp.id, name="s1", command_template="true")
        db.add_all([i0, i1]); await db.flush()
        flow = Flow(project_id=proj.id, name="F"); db.add(flow); await db.flush()
        # Stage 0 (paused step): condition always false. Stage 1: runnable.
        db.add_all([
            FlowStep(flow_id=flow.id, checklist_item_id=i0.id, order_index=0,
                     condition="1 == 2"),
            FlowStep(flow_id=flow.id, checklist_item_id=i1.id, order_index=1),
        ])
        db.add(FlowExecution(id="fx-rs", flow_id=flow.id,
                             status="paused" if paused else "running",
                             paused_at_step=0 if paused else None, variables={},
                             targets=[], host_id=None))
        await db.commit()
        return flow.id


async def _make(paused):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    flow_id = await _seed(maker, paused)
    return engine, maker, flow_id


async def _run_unit():
    engine, maker, flow_id = await _make(False)
    async with maker() as db:
        flow = (await db.execute(select(Flow).where(Flow.id == flow_id))).scalar_one()
        await flow_manager._advance_from_cursor(
            db, flow_execution_id="fx-rs", flow=flow, cursor_index=0,
            base_vars={"flow_id": flow_id}, execution_outputs={},
            item_name_to_exe={}, latest_item_name_to_exe={},
            flow_host_id=None, flow_targets=[], flow_target=None)
        await db.commit()
        rows = (await db.execute(select(Execution).where(
            Execution.flow_execution_id == "fx-rs"))).scalars().all()
        assert any(e.flow_step_index == 1 for e in rows), \
            f"must advance to stage 1, scheduled={[e.flow_step_index for e in rows]}"
    await engine.dispose()
    print("PASS resume advance (unit)")


async def _run_api():
    from unittest.mock import patch
    from app.api.flows import resume_flow_execution
    engine, maker, flow_id = await _make(True)
    with patch("app.core.flow_manager.task_orchestrator") as orch:
        from unittest.mock import AsyncMock

        async def _fake_create(*a, **k):
            ex = Execution(
                public_id="ex-r", item_id=k["item_id"], command="true",
                status=ExecutionStatus.PENDING,
                flow_execution_id=k.get("flow_execution_id"),
                flow_step_index=k.get("flow_step_index"))
            k["db"].add(ex)
            await k["db"].flush()
            return ex

        orch.create_execution_record = AsyncMock(side_effect=_fake_create)
        orch.run_execution_background = AsyncMock(return_value=None)
        async with maker() as db:
            resp = await resume_flow_execution(flow_id, "fx-rs", db, None)
            assert resp["status"] == "resumed", resp
        await asyncio.sleep(0)
        await asyncio.sleep(0)
    async with maker() as db:
        rows = (await db.execute(select(Execution).where(
            Execution.flow_execution_id == "fx-rs"))).scalars().all()
        assert any(e.flow_step_index == 1 for e in rows), \
            f"resume must reach stage 1, got={[e.flow_step_index for e in rows]}"
        st = (await db.execute(
            select(FlowExecution.status).where(FlowExecution.id == "fx-rs"))).scalar()
        assert st == "running", f"flow must be running, got {st}"
    await engine.dispose()
    print("PASS resume advance (api)")


def test_advance_skips_all_skipped_stage():
    asyncio.run(_run_unit())


def test_resume_api_advances_past_skipped():
    asyncio.run(_run_api())


if __name__ == "__main__":
    test_advance_skips_all_skipped_stage()
    test_resume_api_advances_past_skipped()
