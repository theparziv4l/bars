"""Notify/alert failures mid-stream never fail the run."""
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
    item_id, ex_id = await seed_item(maker, alert_patterns={"warning": ["hi"]})
    from app.core.orchestrator import task_orchestrator
    import app.core.orchestrator as orch_mod

    agen = stub_stream([{"stream": "stdout", "text": "hi"}, {"exit_code": 0}])

    async def raise_chunk(*a, **k):
        raise RuntimeError("ws down")

    async def raise_notify(*a, **k):
        raise RuntimeError("ws down")

    notified_final = []

    async def spy_final(exec_id, status, **kw):
        notified_final.append(status)

    with patch.object(orch_mod.settings, "STORAGE_PATH", tmp_path), \
         patch("app.core.host_runner.host_runner.execute_and_stream", agen), \
         patch("app.core.orchestrator.notification_manager") as nm:
        nm.process_output_chunk = AsyncMock(side_effect=raise_chunk)
        nm.notify = AsyncMock(side_effect=raise_notify)
        nm.send_execution_status = AsyncMock(side_effect=spy_final)
        async with maker() as db:
            await task_orchestrator.execute_item(
                db, item_id=item_id, execution_id_override=ex_id)
            await db.commit()
    async with maker() as db:
        row = (await db.execute(select(Execution).where(Execution.id == ex_id))).scalar_one()
        assert row.status == ExecutionStatus.COMPLETED, f"status={row.status} stderr={row.stderr!r}"
        assert "[Internal Error]" not in (row.stderr or "")
    await engine.dispose()
    print("PASS notify isolation")


def test_stream_notify_isolation(tmp_path):
    asyncio.run(_run(tmp_path))


if __name__ == "__main__":
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        asyncio.run(_run(Path(d)))
