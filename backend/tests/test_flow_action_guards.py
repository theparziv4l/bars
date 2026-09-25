"""Flow action guards: same-flow scoping, terminal stop, terminal /next, disabled skip."""
import asyncio
import os
import sys
from unittest.mock import patch, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.main import app
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
        dis = ChecklistItem(group_id=grp.id, name="s-dis", command_template="true",
                            enabled=False)
        db.add_all([item, dis]); await db.flush()
        f1 = Flow(project_id=proj.id, name="F1"); f2 = Flow(project_id=proj.id, name="F2")
        f3 = Flow(project_id=proj.id, name="F3")
        db.add_all([f1, f2, f3]); await db.flush()
        db.add(FlowStep(flow_id=f1.id, checklist_item_id=item.id, order_index=0))
        db.add(FlowStep(flow_id=f3.id, checklist_item_id=dis.id, order_index=0))
        db.add(FlowExecution(id="fx-run", flow_id=f2.id, status="running"))
        db.add(FlowExecution(id="fx-done", flow_id=f1.id, status="completed"))
        db.add(FlowExecution(id="fx-paused", flow_id=f1.id, status="paused",
                             paused_at_step=0))
        db.add(Execution(public_id="ex-done", item_id=item.id, command="true",
                         status=ExecutionStatus.COMPLETED, flow_execution_id="fx-done",
                         flow_step_index=0))
        await db.commit()
        return f1.id, f2.id, f3.id


async def _run():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    f1, f2, f3 = await _seed(maker)

    from app.database import get_db

    async def _override():
        async with maker() as db:
            yield db

    app.dependency_overrides[get_db] = _override
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as cli:
            # Cross-flow actions 404 ...
            r = await cli.post(f"/api/v1/flows/{f1}/execution/fx-run/pause")
            assert r.status_code == 404, f"cross-flow pause: {r.status_code}"
            r = await cli.post(f"/api/v1/flows/{f2}/execution/fx-paused/resume")
            assert r.status_code == 404, f"cross-flow resume: {r.status_code}"
            r = await cli.post(f"/api/v1/flows/{f2}/execution/fx-done/stop")
            assert r.status_code == 404, f"cross-flow stop: {r.status_code}"
            # ... but same-flow actions still work.
            r = await cli.post(f"/api/v1/flows/{f2}/execution/fx-run/pause")
            assert r.status_code == 200, f"same-flow pause: {r.status_code} {r.text[:150]}"
            # Stopping a finished run is a conflict, not a fake success.
            r = await cli.post(f"/api/v1/flows/{f1}/execution/fx-done/stop")
            assert r.status_code == 409, f"terminal stop: {r.status_code}"
            # /next on a finished run reports terminal, not ready/running.
            r = await cli.get("/api/v1/flows/execution/fx-done/next")
            assert r.json()["status"] == "completed", r.text[:200]
    finally:
        app.dependency_overrides.pop(get_db, None)

    # Disabled items never execute via flows.
    from app.core.flow_manager import flow_manager
    created = Execution(id=901, public_id="ex-new", item_id=1, command="true",
                        status=ExecutionStatus.PENDING)
    with patch("app.core.flow_manager.task_orchestrator") as orch:
        orch.create_execution_record = AsyncMock(return_value=created)
        orch.run_execution_background = AsyncMock(return_value=None)
        async with maker() as db:
            fe, started = await flow_manager.start_flow(db, flow_id=f3)
            assert started == [], f"disabled step must not start: {started}"
            orch.create_execution_record.assert_not_called()
        await asyncio.sleep(0)
    async with maker() as db:
        n = (await db.execute(select(func.count()).select_from(Execution))).scalar()
        # Only the seeded ex-done row exists; nothing new was created.
        assert n == 1, f"executions={n}"
    await engine.dispose()
    print("PASS flow action guards")


def test_flow_action_guards():
    asyncio.run(_run())


if __name__ == "__main__":
    test_flow_action_guards()
