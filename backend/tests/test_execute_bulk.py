"""Bulk waits for every item, then raises the first failure."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.core.orchestrator import TaskOrchestrator


async def _run():
    from unittest.mock import MagicMock, patch
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    orch = TaskOrchestrator()
    finished = []

    async def fake_execute_item(session, item_id, *a, **k):
        if item_id == 1:
            raise ValueError("bad item")
        await asyncio.sleep(0.5)
        finished.append(item_id)
        m = MagicMock()
        m.id = item_id
        m.parsed_output = None
        return m

    with patch("app.database.async_session_maker", maker), \
         patch.object(orch, "execute_item", fake_execute_item):
        async with maker() as db:
            try:
                await orch.execute_bulk(db, [1, 2], sequential=False)
                raised = None
            except ValueError as e:
                raised = e
    assert raised is not None and "bad item" in str(raised), "first error must raise"
    assert finished == [2], f"sibling must complete first, finished={finished}"
    await engine.dispose()
    print("PASS bulk waits")


def test_bulk_waits_then_raises():
    asyncio.run(_run())


if __name__ == "__main__":
    test_bulk_waits_then_raises()
