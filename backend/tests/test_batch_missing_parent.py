"""Missing batch parent notifies failed instead of returning silently."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.core.orchestrator import task_orchestrator


async def _run():
    from unittest.mock import patch, AsyncMock
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    notified = []
    with patch("app.database.async_session_maker", maker), \
         patch("app.core.orchestrator.notification_manager") as nm:
        nm.send_execution_status = AsyncMock(
            side_effect=lambda eid, st, **k: notified.append((eid, st)))
        # execution_id 424242 does not exist -> early return path.
        await task_orchestrator._run_batch_execution(
            execution_id=424242, targets=["t1"], item_id=1, base_variables={})
    assert (424242, "failed") in notified, f"notified={notified}"
    # Tracking entry cleaned up.
    assert 424242 not in task_orchestrator._running_tasks
    await engine.dispose()
    print("PASS missing parent")


def test_missing_parent_notifies_failed():
    asyncio.run(_run())


if __name__ == "__main__":
    test_missing_parent_notifies_failed()
