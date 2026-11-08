"""execute_item finalize: no write txn across extractors, snapshot refresh, stdin wiring,
fail-fast on unresolved variables (items 7, 16, 25, fail-fast)."""
import asyncio

from sqlalchemy import select, text
from unittest.mock import patch

import orch_lc
from orch_fixture import seed_item, stub_stream

from app.models.execution import Execution, ExecutionStatus


def test_extractor_runs_without_write_lock_and_gets_stdin(tmp_path):
    async def run():
        engine, maker = await orch_lc.make_maker(tmp_path, busy_ms=300)
        item_id, ex_id = await seed_item(maker, output_regex={"x": "cmd:wc -l"})
        probe = {}
        seen = {}

        async def fake_sync(cmd, cwd=None, timeout=60, **kw):
            seen["cmd"], seen["kw"] = cmd, kw
            try:
                async with maker() as p:
                    await p.execute(text("UPDATE executions SET stderr = stderr WHERE id = -1"))
                    await p.commit()
                probe["locked"] = False
            except Exception as e:  # database is locked
                probe["locked"] = True
                probe["err"] = str(e)
            return {"exit_code": 0, "stdout": "1", "stderr": ""}

        from app.core.orchestrator import task_orchestrator
        stream = stub_stream([{"stream": "stdout", "text": "hi"}, {"exit_code": 0}])
        with orch_lc.env(maker, tmp_path, stream=stream, execute_sync=fake_sync):
            async with maker() as db:
                await task_orchestrator.execute_item(
                    db, item_id=item_id, execution_id_override=ex_id)
                await db.commit()
        assert probe["locked"] is False, f"write txn held across extractor: {probe}"
        assert seen["kw"].get("stdin") == "hi\n", seen
        await engine.dispose()

    asyncio.run(run())


def test_parsed_output_survives_later_savepoint_failure(tmp_path):
    async def run():
        engine, maker = await orch_lc.make_maker(tmp_path)
        item_id, ex_id = await seed_item(maker, output_regex={"x": "hi"})
        from app.core.orchestrator import task_orchestrator

        async def boom(*a, **k):
            raise RuntimeError("discovery exploded")

        stream = stub_stream([{"stream": "stdout", "text": "hi"}, {"exit_code": 0}])
        with orch_lc.env(maker, tmp_path, stream=stream), \
             patch.object(task_orchestrator, "_process_asset_discovery", boom):
            async with maker() as db:
                await task_orchestrator.execute_item(
                    db, item_id=item_id, execution_id_override=ex_id)
                await db.commit()
        async with maker() as db:
            row = (await db.execute(select(Execution).where(Execution.id == ex_id))).scalar_one()
            assert row.status == ExecutionStatus.COMPLETED
            assert row.parsed_output and "x" in row.parsed_output, row.parsed_output
        await engine.dispose()

    asyncio.run(run())


def test_unresolved_variables_fail_fast(tmp_path):
    async def run():
        engine, maker = await orch_lc.make_maker(tmp_path)
        item_id, ex_id = await seed_item(maker, command_template="echo {nope}")
        from app.core.orchestrator import task_orchestrator
        called = []

        async def stream(*a, **k):
            called.append(1)
            yield {"exit_code": 0}

        with orch_lc.env(maker, tmp_path, stream=stream) as log:
            async with maker() as db:
                ex = await task_orchestrator.execute_item(
                    db, item_id=item_id, execution_id_override=ex_id)
                await db.commit()
            await asyncio.sleep(0)
        assert not called, "command must not reach the agent"
        async with maker() as db:
            row = (await db.execute(select(Execution).where(Execution.id == ex_id))).scalar_one()
            assert row.status == ExecutionStatus.FAILED
            assert row.stderr == "Unresolved variables: {nope}", row.stderr
            assert row.completed_at is not None
        assert ex.status == ExecutionStatus.FAILED
        assert log and log[-1][1] == "failed", log
        assert ex_id not in task_orchestrator._running_tasks
        await engine.dispose()

    asyncio.run(run())
