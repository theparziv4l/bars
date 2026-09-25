"""Temp files are cleaned even when finalize raises."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401

from orch_fixture import seed_item, stub_stream


async def _run(tmp_path):
    from unittest.mock import patch
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    item_id, ex_id = await seed_item(maker, output_regex={"x": "hi"})
    tmpdir = tmp_path / "tmp"
    tmpdir.mkdir()
    (tmpdir / f"batch_{ex_id}_targets.txt").write_text("10.0.0.1")
    (tmpdir / f"exec_{ex_id}_vars.json").write_text("{}")

    from app.core.orchestrator import task_orchestrator
    import app.core.orchestrator as orch_mod

    async def boom(*a, **k):
        raise RuntimeError("injection exploded")

    agen = stub_stream([{"stream": "stdout", "text": "hi"}, {"exit_code": 0}])
    with patch.object(orch_mod.settings, "STORAGE_PATH", tmp_path), \
         patch("app.core.host_runner.host_runner.execute_and_stream", agen), \
         patch.object(task_orchestrator, "_process_variable_injection", boom):
        async with maker() as db:
            # C4 tames the raise; C3 asserts cleanup happened regardless.
            await task_orchestrator.execute_item(
                db, item_id=item_id, execution_id_override=ex_id)
    assert list(tmpdir.iterdir()) == [], f"leftovers: {list(tmpdir.iterdir())}"
    await engine.dispose()
    print("PASS tmp cleanup")


def test_tmp_cleanup(tmp_path):
    asyncio.run(_run(tmp_path))


if __name__ == "__main__":
    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as d:
        asyncio.run(_run(Path(d)))
