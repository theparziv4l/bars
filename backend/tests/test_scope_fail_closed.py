"""Unresolvable project scope refuses to launch target-bearing runs."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.core.orchestrator import task_orchestrator
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.execution import Execution, ExecutionStatus
from app.models.project import Project


async def _run():
    from unittest.mock import patch
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        proj = Project(name="P"); db.add(proj); await db.flush()
        grp = ChecklistGroup(project_id=None, name="TemplateG"); db.add(grp); await db.flush()
        item = ChecklistItem(group_id=grp.id, name="I", command_template="ping {target}")
        db.add(item); await db.flush()
        ex = Execution(public_id="ex-scope", item_id=item.id, command="(pending)",
                       status=ExecutionStatus.PENDING)
        db.add(ex); await db.commit()
        ex_id, item_id = ex.id, item.id
    # run_execution_background imports async_session_maker inside its functions,
    # so patching app.database redirects it to our memory engine.
    with patch("app.database.async_session_maker", maker):
        await task_orchestrator.run_execution_background(
            ex_id, item_id, variables={"target": "10.0.0.2", "flow_id": "424242"})
    async with maker() as db:
        row = (await db.execute(select(Execution).where(Execution.id == ex_id))).scalar_one()
        assert row.status == ExecutionStatus.FAILED, f"status={row.status}"
        assert "Cannot determine project scope" in (row.stderr or ""), \
            f"must refuse, stderr={row.stderr!r}"
    await engine.dispose()
    print("PASS scope")


def test_scope_fail_closed():
    asyncio.run(_run())


if __name__ == "__main__":
    test_scope_fail_closed()
