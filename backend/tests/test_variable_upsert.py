"""Parallel injection writes converge on one row per scope (LWW, no dupes)."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.core.orchestrator import task_orchestrator
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.execution import Execution, ExecutionStatus
from app.models.project import Project
from app.models.variable import ProjectVariable


class _R:
    def __init__(self, value):
        self.value = value


async def _run():
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    # File DB (not :memory:): each session needs its own connection, otherwise
    # the two writers share one connection/transaction and savepoint
    # interleavings detach instances instead of colliding on the index.
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp.name}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        proj = Project(name="P"); db.add(proj); await db.flush()
        grp = ChecklistGroup(project_id=proj.id, name="G"); db.add(grp); await db.flush()
        item = ChecklistItem(group_id=grp.id, name="s0", command_template="true",
                             storage_policy={"variable_mapping": {"x": "ports"}})
        db.add(item); await db.flush()
        e1 = Execution(public_id="ex-v1", item_id=item.id, command="true",
                       status=ExecutionStatus.COMPLETED)
        e2 = Execution(public_id="ex-v2", item_id=item.id, command="true",
                       status=ExecutionStatus.COMPLETED)
        db.add_all([e1, e2]); await db.commit()
        item_id, project_id = item.id, proj.id

    from sqlalchemy.orm import selectinload

    async def inject(ex_pub, val):
        async with maker() as db:
            item = (await db.execute(select(ChecklistItem).where(
                ChecklistItem.id == item_id).options(
                    selectinload(ChecklistItem.group)))).scalar_one()
            ex = (await db.execute(select(Execution).where(
                Execution.public_id == ex_pub))).scalar_one()
            await task_orchestrator._process_variable_injection(
                db, ex, item, {"x": _R(val)})
            await db.commit()

    await asyncio.gather(inject("ex-v1", "80"), inject("ex-v2", "443"))
    async with maker() as db:
        n = (await db.execute(select(func.count()).select_from(ProjectVariable).where(
            ProjectVariable.project_id == project_id,
            ProjectVariable.key == "ports"))).scalar()
        assert n == 1, f"exactly one row per scope, got {n}"
        val = (await db.execute(select(ProjectVariable.value).where(
            ProjectVariable.project_id == project_id,
            ProjectVariable.key == "ports"))).scalar()
        assert val in ("80", "443"), f"LWW value, got {val!r}"
    await engine.dispose()
    os.unlink(tmp.name)
    print("PASS variable upsert")


def test_parallel_injection_converges():
    asyncio.run(_run())


if __name__ == "__main__":
    test_parallel_injection_converges()
