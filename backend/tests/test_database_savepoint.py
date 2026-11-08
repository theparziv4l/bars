"""SAVEPOINT semantics on the aiosqlite engine (SQLAlchemy documented recipe)."""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import create_async_engine

import app.database as database


def _engine(path):
    eng = create_async_engine(f"sqlite+aiosqlite:///{path}")
    # Same hooks the app engine uses.
    event.listen(eng.sync_engine, "connect", database.set_sqlite_pragma)
    event.listen(eng.sync_engine, "begin", database.do_begin)
    return eng


async def _rows(eng):
    async with eng.connect() as c:
        return sorted(r[0] for r in (await c.execute(text("SELECT v FROM t"))).all())


async def _run():
    with tempfile.TemporaryDirectory() as d:
        eng = _engine(os.path.join(d, "t.db"))
        async with eng.begin() as c:
            await c.execute(text("CREATE TABLE t (v TEXT)"))

        # 1. savepoint rollback keeps earlier outer writes, drops only its own.
        async with eng.connect() as c:
            await c.execute(text("INSERT INTO t VALUES ('outer')"))
            sp = await c.begin_nested()
            await c.execute(text("INSERT INTO t VALUES ('inner')"))
            await sp.rollback()
            await c.commit()
        assert await _rows(eng) == ["outer"], await _rows(eng)

        # 2. leading savepoint (first statement) rollback, then outer write.
        async with eng.connect() as c:
            sp = await c.begin_nested()
            await c.execute(text("INSERT INTO t VALUES ('lead')"))
            await sp.rollback()
            await c.execute(text("INSERT INTO t VALUES ('after')"))
            await c.commit()
        assert await _rows(eng) == ["after", "outer"], await _rows(eng)

        # 3. releasing a savepoint must NOT commit the outer transaction.
        async with eng.connect() as c:
            await c.execute(text("INSERT INTO t VALUES ('o2')"))
            sp = await c.begin_nested()
            await c.execute(text("INSERT INTO t VALUES ('i2')"))
            await sp.commit()  # RELEASE
            await c.rollback()
        assert await _rows(eng) == ["after", "outer"], await _rows(eng)

        # 4. leading savepoint released, outer rolled back -> nothing persists.
        async with eng.connect() as c:
            sp = await c.begin_nested()
            await c.execute(text("INSERT INTO t VALUES ('i3')"))
            await sp.commit()
            await c.rollback()
        assert await _rows(eng) == ["after", "outer"], await _rows(eng)

        # WAL pragma must still have been applied.
        async with eng.connect() as c:
            mode = (await c.execute(text("PRAGMA journal_mode"))).scalar()
        assert mode == "wal", mode
        await eng.dispose()


def test_savepoint_semantics():
    asyncio.run(_run())


def test_app_engine_has_hooks():
    assert event.contains(database.engine.sync_engine, "begin", database.do_begin)
