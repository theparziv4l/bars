"""Cancelling a batch marks the parent CANCELLED, notifies, and propagates."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.models.execution import Execution, ExecutionStatus

from orch_fixture import seed_item


async def _run():
    from unittest.mock import patch, AsyncMock
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    item_id, ex_id = await seed_item(maker)
    from app.core.orchestrator import task_orchestrator

    async def slow_child(*a, **k):
        await asyncio.sleep(30)
        return None

    notified = []
    with patch("app.database.async_session_maker", maker), \
         patch.object(task_orchestrator, "execute_item", slow_child), \
         patch("app.core.orchestrator.notification_manager") as nm:
        nm.send_execution_status = AsyncMock(
            side_effect=lambda eid, st, **k: notified.append(st))
        nm.send_project_update = AsyncMock(return_value=None)
        task = asyncio.create_task(task_orchestrator._run_batch_execution(
            execution_id=ex_id, targets=["t1", "t2"], item_id=item_id,
            base_variables={}))
        await asyncio.sleep(0.2)
        task.cancel()
        try:
            await task
            propagated = False
        except asyncio.CancelledError:
            propagated = True
    assert propagated, "cancellation must propagate"
    async with maker() as db:
        st = (await db.execute(select(Execution.status).where(Execution.id == ex_id))).scalar()
        assert st == ExecutionStatus.CANCELLED, f"parent stuck at {st}"
    assert "cancelled" in notified, f"notified={notified}"
    await engine.dispose()
    print("PASS batch cancel")


def test_batch_cancel():
    asyncio.run(_run())


if __name__ == "__main__":
    test_batch_cancel()
