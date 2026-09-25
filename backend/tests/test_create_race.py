"""Concurrent same-scope creates: exactly one winner, loser ValueError."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.core.orchestrator import TaskOrchestrator
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.execution import Execution
from app.models.project import Project


async def _run():
    from unittest.mock import patch, AsyncMock
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        proj = Project(name="P"); db.add(proj); await db.flush()
        grp = ChecklistGroup(project_id=proj.id, name="G"); db.add(grp); await db.flush()
        db.add(ChecklistItem(group_id=grp.id, name="s0", command_template="true"))
        await db.commit()
        item = (await db.execute(select(ChecklistItem))).scalar_one()
        item_id = item.id
    orch = TaskOrchestrator()
    with patch("app.core.orchestrator.notification_manager") as nm:
        nm.send_execution_status = AsyncMock(return_value=None)

        async def one():
            async with maker() as db:
                try:
                    await orch.create_execution_record(db, item_id=item_id)
                    await db.commit()
                    return "ok"
                except ValueError:
                    return "dup"

        results = await asyncio.gather(one(), one())
    assert sorted(results) == ["dup", "ok"], f"results={results}"
    async with maker() as db:
        n = (await db.execute(select(func.count()).select_from(Execution))).scalar()
        assert n == 1, f"exactly one row, got {n}"
    await engine.dispose()
    print("PASS create race")


def test_concurrent_create_single_winner():
    asyncio.run(_run())


if __name__ == "__main__":
    test_concurrent_create_single_winner()
