"""Restart sweep leaves PENDING queued, cancels RUNNING, emits sync events."""
import asyncio
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.execution import Execution, ExecutionStatus
from app.models.project import Project
from app.models.sync import SyncEvent


async def _run():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        proj = Project(name="P"); db.add(proj); await db.flush()
        grp = ChecklistGroup(project_id=proj.id, name="G"); db.add(grp); await db.flush()
        item = ChecklistItem(group_id=grp.id, name="I", command_template="true")
        db.add(item); await db.flush()
        pend = Execution(public_id="ex-pend", item_id=item.id, command="true",
                         status=ExecutionStatus.PENDING)
        run = Execution(public_id="ex-run", item_id=item.id, command="true",
                        status=ExecutionStatus.RUNNING, started_at=datetime.utcnow())
        db.add_all([pend, run]); await db.commit()

        from app.main import _cleanup_orphan_executions
        maker = async_sessionmaker(engine, expire_on_commit=False)
        await _cleanup_orphan_executions(_session_maker=maker)
        db.expire_all()  # sweep committed via another session; drop identity-map cache

        st = {e.public_id: e.status for e in (await db.execute(select(Execution))).scalars()}
        assert st["ex-pend"] == ExecutionStatus.PENDING, f"PENDING must stay queued: {st}"
        assert st["ex-run"] == ExecutionStatus.CANCELLED, f"RUNNING must cancel: {st}"
        n = (await db.execute(select(func.count()).select_from(SyncEvent).where(
            SyncEvent.entity_type == "executions"))).scalar()
        assert n == 1, f"one sync event for the swept run, got {n}"
    await engine.dispose()
    print("PASS sweep")


def test_restart_sweep_running_only_with_event():
    asyncio.run(_run())


if __name__ == "__main__":
    test_restart_sweep_running_only_with_event()
