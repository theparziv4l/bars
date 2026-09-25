"""Finalize-stage failures still persist a terminal status and notify."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.models.execution import Execution, ExecutionStatus

from orch_fixture import seed_item, stub_stream


async def _run(tmp_path):
    from unittest.mock import patch, AsyncMock
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    item_id, ex_id = await seed_item(
        maker, output_regex={"x": "hi"},
        storage_policy={"variable_mapping": {"x": "y"}})
    from app.core.orchestrator import task_orchestrator
    import app.core.orchestrator as orch_mod

    async def boom(*a, **k):
        raise RuntimeError("injection exploded")

    notified = []
    agen = stub_stream([{"stream": "stdout", "text": "hi"}, {"exit_code": 0}])

    async def spy_notify(exec_id, status, **kw):
        notified.append((exec_id, status))

    with patch.object(orch_mod.settings, "STORAGE_PATH", tmp_path), \
         patch("app.core.host_runner.host_runner.execute_and_stream", agen), \
         patch.object(task_orchestrator, "_process_variable_injection", boom), \
         patch("app.core.orchestrator.notification_manager") as nm:
        nm.send_execution_status = AsyncMock(side_effect=spy_notify)
        nm.process_output_chunk = AsyncMock(return_value=[])
        nm.notify = AsyncMock(return_value=None)
        async with maker() as db:
            await task_orchestrator.execute_item(
                db, item_id=item_id, execution_id_override=ex_id)
            await db.commit()
    async with maker() as db:
        st = (await db.execute(select(Execution.status).where(Execution.id == ex_id))).scalar()
        assert st != ExecutionStatus.RUNNING, f"stranded RUNNING: {st}"
    assert notified and notified[-1][1] != "running", f"notified={notified}"
    await engine.dispose()
    print("PASS finalize degrades")


def test_finalize_degrades(tmp_path):
    asyncio.run(_run(tmp_path))


if __name__ == "__main__":
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        asyncio.run(_run(Path(d)))
