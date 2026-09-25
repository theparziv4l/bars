"""RUNNING is committed (visible to other sessions) before the stream starts."""
import asyncio
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select, event as sa_event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.models.execution import Execution, ExecutionStatus

from orch_fixture import seed_item


class _NM:
    async def send_execution_status(self, *a, **k):
        return None

    async def notify(self, *a, **k):
        return None

    async def process_output_chunk(self, *a, **k):
        return []

    async def send_project_update(self, *a, **k):
        return None


async def _run(tmp_path):
    from unittest.mock import patch
    from app.core.orchestrator import task_orchestrator
    import app.core.orchestrator as orch_mod

    assert "commit_transaction" not in inspect.signature(
        task_orchestrator.execute_item).parameters, "trap param must be gone"

    db_path = tmp_path / "c7.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")

    @sa_event.listens_for(engine.sync_engine, "connect")
    def _pragmas(conn, rec):
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=30000")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    item_id, ex_id = await seed_item(maker)

    gate = asyncio.Event()
    seen_during_stream = {}

    async def gated_stream(*a, **k):
        async with maker() as probe:
            row = (await probe.execute(
                select(Execution.status).where(Execution.id == ex_id))).scalar()
            seen_during_stream["status"] = row
        gate.set()
        yield {"exit_code": 0}

    with patch.object(orch_mod.settings, "STORAGE_PATH", tmp_path), \
         patch("app.core.host_runner.host_runner.execute_and_stream", gated_stream), \
         patch("app.core.orchestrator.notification_manager", _NM()):
        async with maker() as db:
            await task_orchestrator.execute_item(
                db, item_id=item_id, execution_id_override=ex_id)
            await db.commit()
    assert gate.is_set()
    assert seen_during_stream["status"] == ExecutionStatus.RUNNING, \
        f"RUNNING must be committed pre-stream, saw {seen_during_stream}"
    await engine.dispose()
    print("PASS commit before stream")


def test_commit_before_stream(tmp_path):
    asyncio.run(_run(tmp_path))


if __name__ == "__main__":
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        asyncio.run(_run(Path(d)))
