"""notify_after_commit: fan-out runs only after the outer commit, never on rollback."""
import asyncio

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.notifications import notify_after_commit


async def _run():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    calls = []

    async def note(tag):
        calls.append(tag)

    async with AsyncSession(engine) as db:
        await db.execute(text("select 1"))
        notify_after_commit(db, note, "committed")
        async with db.begin_nested():
            notify_after_commit(db, note, "in-savepoint")
        await asyncio.sleep(0)
        assert calls == []  # nothing before commit
        await db.commit()
        await asyncio.sleep(0.01)
        assert sorted(calls) == ["committed", "in-savepoint"]

        calls.clear()
        await db.execute(text("select 1"))
        notify_after_commit(db, note, "rolled-back")
        await db.rollback()
        await db.commit()
        await asyncio.sleep(0.01)
        assert calls == []
    await engine.dispose()


def test_notify_after_commit():
    asyncio.run(_run())
