"""Shared harness for the orchestrator lifecycle tests (not collected)."""
import contextlib
import os
import sys
from unittest.mock import AsyncMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import event as sa_event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database import Base
import app.models  # noqa: F401


async def make_maker(tmp_path, name="lc.db", busy_ms=30000):
    """File-backed WAL engine so a second connection can probe write locks."""
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / name}")

    @sa_event.listens_for(engine.sync_engine, "connect")
    def _pragmas(conn, rec):
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute(f"PRAGMA busy_timeout={busy_ms}")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


@contextlib.contextmanager
def env(maker, tmp_path, stream=None, execute_sync=None):
    """Patch storage, DB maker, notifications, agent stream/sync. Yields notif log."""
    import app.core.orchestrator as orch_mod

    log = []

    async def spy(exec_id, status, **kw):
        log.append((exec_id, status, kw))

    stack = contextlib.ExitStack()
    stack.enter_context(patch.object(orch_mod.settings, "STORAGE_PATH", tmp_path))
    stack.enter_context(patch("app.database.async_session_maker", maker))
    nm = stack.enter_context(patch("app.core.orchestrator.notification_manager"))
    nm.send_execution_status = AsyncMock(side_effect=spy)
    nm.send_project_update = AsyncMock(return_value=None)
    nm.process_output_chunk = AsyncMock(return_value=[])
    nm.notify = AsyncMock(return_value=None)
    if stream is not None:
        stack.enter_context(
            patch("app.core.host_runner.host_runner.execute_and_stream", stream))
    if execute_sync is not None:
        stack.enter_context(
            patch("app.core.host_runner.host_runner.execute_sync", execute_sync))
    try:
        yield log
    finally:
        stack.close()
