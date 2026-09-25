"""Cancel flags parent+children via context map; agent failure is surfaced."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.core.orchestrator import TaskContext, TaskOrchestrator
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.execution import Execution, ExecutionStatus
from app.models.project import Project


async def _seed(maker):
    async with maker() as db:
        proj = Project(name="P"); db.add(proj); await db.flush()
        grp = ChecklistGroup(project_id=proj.id, name="G"); db.add(grp); await db.flush()
        item = ChecklistItem(group_id=grp.id, name="I", command_template="true")
        db.add(item); await db.flush()
        parent = Execution(public_id="ex-p", item_id=item.id, command="batch",
                           status=ExecutionStatus.RUNNING)
        child = Execution(public_id="ex-c", item_id=item.id, command="true",
                          status=ExecutionStatus.RUNNING,
                          variables_used={"parent_execution_id": None})  # fixed below
        db.add_all([parent, child]); await db.flush()
        child.variables_used = {"parent_execution_id": parent.id}
        await db.commit()
        return parent.id, child.id


async def _run():
    from unittest.mock import patch
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    parent_id, child_id = await _seed(maker)

    orch = TaskOrchestrator()
    orch._running_tasks[parent_id] = TaskContext(execution_id=parent_id)
    orch._running_tasks[child_id] = TaskContext(
        execution_id=child_id, parent_execution_id=parent_id)

    agent_calls = []

    async def fake_cancel(exec_id):
        agent_calls.append(exec_id)
        return True

    with patch("app.core.host_runner.host_runner.cancel_execution", fake_cancel), \
         patch("app.database.async_session_maker", maker):
        assert await orch.cancel_execution(parent_id) is True
    assert orch._running_tasks[child_id].cancelled is True, "child must be flagged"
    assert str(child_id) in agent_calls and str(parent_id) in agent_calls

    # Agent failure path: flagged anyway, warning persisted, still True.
    orch2 = TaskOrchestrator()
    orch2._running_tasks[parent_id] = TaskContext(execution_id=parent_id)

    async def failing_cancel(exec_id):
        return False

    with patch("app.core.host_runner.host_runner.cancel_execution", failing_cancel), \
         patch("app.database.async_session_maker", maker):
        assert await orch2.cancel_execution(parent_id) is True
    async with maker() as db:
        row = (await db.execute(select(Execution).where(Execution.id == parent_id))).scalar_one()
        assert "Agent-side cancel failed" in (row.stderr or ""), f"stderr={row.stderr!r}"
    await engine.dispose()
    print("PASS cancel")


def test_cancel_execution():
    asyncio.run(_run())


if __name__ == "__main__":
    test_cancel_execution()
