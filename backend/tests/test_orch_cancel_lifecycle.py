"""Cancellation / cleanup lifecycle of execute_item (items 9, 10, 12, 57, 58)."""
import asyncio
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select, update

import orch_lc
from orch_fixture import seed_item

from app.core.orchestrator import TaskContext, TaskOrchestrator
from app.models.execution import Execution, ExecutionStatus


def _counting_stream(called, events=None, gate=None):
    async def stream(*a, **k):
        called.append(1)
        if gate is not None:
            await gate.wait()
        for e in events or [{"exit_code": 0}]:
            yield dict(e)

    return stream


async def _status(maker, ex_id):
    async with maker() as db:
        return (await db.execute(select(Execution).where(Execution.id == ex_id))).scalar_one()


def test_db_cancelled_row_is_not_run(tmp_path):
    async def run():
        engine, maker = await orch_lc.make_maker(tmp_path)
        item_id, ex_id = await seed_item(maker)
        t0 = datetime(2020, 1, 1)
        async with maker() as db:
            await db.execute(update(Execution).where(Execution.id == ex_id).values(
                status=ExecutionStatus.CANCELLED, completed_at=t0))
            await db.commit()
        orch, called = TaskOrchestrator(), []
        with orch_lc.env(maker, tmp_path, stream=_counting_stream(called)):
            async with maker() as db:
                await orch.execute_item(db, item_id=item_id, execution_id_override=ex_id)
                await db.commit()
        assert not called, "CANCELLED row must not run"
        row = await _status(maker, ex_id)
        assert row.status == ExecutionStatus.CANCELLED and row.completed_at == t0
        await engine.dispose()

    asyncio.run(run())


def test_cancel_in_db_during_run_not_overwritten_and_keeps_completed_at(tmp_path):
    async def run():
        engine, maker = await orch_lc.make_maker(tmp_path)
        item_id, ex_id = await seed_item(maker)
        t0 = datetime(2020, 1, 1)
        orch = TaskOrchestrator()

        async def stream(*a, **k):
            async with maker() as db:  # a DB-only cancel (pause / stale path)
                await db.execute(update(Execution).where(Execution.id == ex_id).values(
                    status=ExecutionStatus.CANCELLED, completed_at=t0))
                await db.commit()
            yield {"exit_code": 0}

        with orch_lc.env(maker, tmp_path, stream=stream):
            async with maker() as db:
                ex = await orch.execute_item(db, item_id=item_id, execution_id_override=ex_id)
                await db.commit()
        row = await _status(maker, ex_id)
        assert row.status == ExecutionStatus.CANCELLED, row.status
        assert row.completed_at == t0, row.completed_at
        assert ex.status == ExecutionStatus.CANCELLED
        await engine.dispose()

    asyncio.run(run())


def test_cancel_while_queued_on_semaphore_never_streams(tmp_path):
    async def run():
        engine, maker = await orch_lc.make_maker(tmp_path)
        item_id, ex_id = await seed_item(maker)
        orch, called = TaskOrchestrator(max_concurrent=1), []
        await orch._semaphore.acquire()  # occupy the only slot
        with orch_lc.env(maker, tmp_path, stream=_counting_stream(called)), \
             patch("app.core.host_runner.host_runner.cancel_execution",
                   AsyncMock(return_value=True)):
            async def go():
                async with maker() as db:
                    await orch.execute_item(db, item_id=item_id, execution_id_override=ex_id)
                    await db.commit()

            task = asyncio.create_task(go())
            for _ in range(200):
                if ex_id in orch._running_tasks:
                    break
                await asyncio.sleep(0.01)
            assert await orch.cancel_execution(ex_id) is True
            orch._semaphore.release()
            await task
        assert not called, "run cancelled while queued must not start"
        assert (await _status(maker, ex_id)).status == ExecutionStatus.CANCELLED
        await engine.dispose()

    asyncio.run(run())


def test_task_cancellation_is_reraised_after_finalize(tmp_path):
    async def run():
        engine, maker = await orch_lc.make_maker(tmp_path)
        item_id, ex_id = await seed_item(maker)
        orch, called, gate = TaskOrchestrator(), [], asyncio.Event()
        with orch_lc.env(maker, tmp_path, stream=_counting_stream(called, gate=gate)):
            async def go():
                async with maker() as db:
                    await orch.execute_item(db, item_id=item_id, execution_id_override=ex_id)

            task = asyncio.create_task(go())
            for _ in range(200):
                if called:
                    break
                await asyncio.sleep(0.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        row = await _status(maker, ex_id)
        assert row.status == ExecutionStatus.CANCELLED, row.status
        assert ex_id not in orch._running_tasks
        await engine.dispose()

    asyncio.run(run())


def test_context_and_tempfiles_not_leaked_when_prestream_step_raises(tmp_path):
    async def run():
        engine, maker = await orch_lc.make_maker(tmp_path)
        item_id, ex_id = await seed_item(maker, command_template="cat {targets}")
        orch, called = TaskOrchestrator(), []
        with orch_lc.env(maker, tmp_path, stream=_counting_stream(called)):
            import app.core.orchestrator as orch_mod

            async def boom(*a, **k):
                raise RuntimeError("notify exploded")

            orch_mod.notification_manager.send_execution_status = AsyncMock(side_effect=boom)
            async with maker() as db:
                with pytest.raises(RuntimeError):
                    await orch.execute_item(
                        db, item_id=item_id, execution_id_override=ex_id,
                        variables={"targets": ["10.0.0.1", "10.0.0.2"]})
        assert ex_id not in orch._running_tasks, "context leaked"
        assert not list((tmp_path / "tmp").glob(f"*{ex_id}*")), "temp files leaked"
        await engine.dispose()

    asyncio.run(run())


def test_child_of_cancelled_batch_parent_never_streams(tmp_path):
    async def run():
        engine, maker = await orch_lc.make_maker(tmp_path)
        item_id, ex_id = await seed_item(maker)
        orch, called = TaskOrchestrator(), []
        orch._running_tasks[999] = TaskContext(execution_id=999, cancelled=True)
        with orch_lc.env(maker, tmp_path, stream=_counting_stream(called)):
            async with maker() as db:
                await orch.execute_item(
                    db, item_id=item_id, execution_id_override=ex_id,
                    variables={"parent_execution_id": 999})
                await db.commit()
        assert not called, "child must abort when its parent was cancelled mid-setup"
        assert (await _status(maker, ex_id)).status == ExecutionStatus.CANCELLED
        await engine.dispose()

    asyncio.run(run())


def test_batch_child_in_setup_aborts_when_parent_cancelled(tmp_path):
    async def run():
        engine, maker = await orch_lc.make_maker(tmp_path)
        item_id, parent_id = await seed_item(maker)
        orch, called = TaskOrchestrator(), []
        real_create = orch.create_execution_record

        async def create_then_cancel(*a, **k):
            rec = await real_create(*a, **k)
            orch._running_tasks[parent_id].cancelled = True  # cancel lands mid-setup
            return rec

        with orch_lc.env(maker, tmp_path, stream=_counting_stream(called)), \
             patch.object(orch, "create_execution_record", create_then_cancel):
            await orch._run_batch_execution(
                execution_id=parent_id, targets=["10.0.0.1", "10.0.0.2"],
                item_id=item_id, base_variables={})
        assert not called, "child in setup must not start once the batch is cancelled"
        assert (await _status(maker, parent_id)).status == ExecutionStatus.CANCELLED
        async with maker() as db:
            kids = (await db.execute(
                select(Execution).where(Execution.id != parent_id))).scalars().all()
        assert kids == [], "half-made child rows must not be committed"
        await engine.dispose()

    asyncio.run(run())
