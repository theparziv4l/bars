"""Pre-flight / record creation / variable resolution (items 8, 11, 20, 37, 43, 54)."""
import asyncio
import uuid
from datetime import datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import event as sa_event, select

import orch_lc
from orch_fixture import seed_item, stub_stream

from app.core.orchestrator import TaskOrchestrator
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.execution import Execution, ExecutionOutput, ExecutionStatus
from app.models.host import Host
from app.models.project import Project


async def _finish_seed_exec(maker, ex_id):
    """seed_item leaves a PENDING row; the duplicate guard would trip on it."""
    async with maker() as db:
        row = (await db.execute(select(Execution).where(Execution.id == ex_id))).scalar_one()
        row.status = ExecutionStatus.COMPLETED
        await db.commit()


async def _seed_project(maker, template="echo hi", n_items=1):
    async with maker() as db:
        proj = Project(name=f"P-{uuid.uuid4().hex[:6]}")
        db.add(proj)
        await db.flush()
        grp = ChecklistGroup(project_id=proj.id, name="G")
        db.add(grp)
        await db.flush()
        items = []
        for i in range(n_items):
            it = ChecklistItem(group_id=grp.id, name=f"I{i}", command_template=template)
            db.add(it)
            items.append(it)
        await db.flush()
        await db.commit()
        return proj.id, [i.id for i in items]


def _ok_stream(calls):
    async def stream(command, *a, **k):
        calls.append(command)
        yield {"stream": "stdout", "text": "ok"}
        yield {"exit_code": 0}

    return stream


def test_preflight_error_marks_execution_failed(tmp_path):
    async def run():
        engine, maker = await orch_lc.make_maker(tmp_path)
        item_id, ex_id = await seed_item(maker)
        orch = TaskOrchestrator()

        async def boom(*a, **k):
            raise RuntimeError("db exploded")

        with orch_lc.env(maker, tmp_path) as log, \
             patch("app.core.orchestrator.excluded_targets", boom):
            await orch.run_execution_background(ex_id, item_id)
            await asyncio.sleep(0)
        async with maker() as db:
            row = (await db.execute(select(Execution).where(Execution.id == ex_id))).scalar_one()
        assert row.status == ExecutionStatus.FAILED, row.status
        assert "db exploded" in (row.stderr or "")
        assert log and log[-1][1] == "failed"
        await engine.dispose()

    asyncio.run(run())


def test_execute_bulk_applies_scope_exclusion_and_batching(tmp_path):
    async def run():
        engine, maker = await orch_lc.make_maker(tmp_path)
        proj_id, (item_id,) = await _seed_project(maker, template="scan {target}")
        async with maker() as db:
            db.add(Host(project_id=proj_id, ip_address="10.0.0.9", excluded=True))
            await db.commit()
        orch, calls = TaskOrchestrator(), []
        with orch_lc.env(maker, tmp_path, stream=_ok_stream(calls)):
            async with maker() as db:
                # excluded target: refused, never reaches the agent
                res = await orch.execute_bulk(db, [item_id], variables={"target": "10.0.0.9"})
                assert [e.status for e in res] == [ExecutionStatus.FAILED]
                assert not calls
                assert "excluded" in (res[0].stderr or "")
                # multi-target: split into one child per target
                res = await orch.execute_bulk(
                    db, [item_id], variables={"target": "10.0.0.1 10.0.0.2"})
                assert len(res) == 1
                assert sorted(calls) == ["scan 10.0.0.1", "scan 10.0.0.2"], calls
        await engine.dispose()

    asyncio.run(run())


def test_batch_child_host_lookup_ignores_deleted_and_duplicates(tmp_path):
    async def run():
        engine, maker = await orch_lc.make_maker(tmp_path)
        proj_id, (item_id,) = await _seed_project(maker, template="scan {target}")
        async with maker() as db:
            dead = Host(project_id=proj_id, ip_address="10.0.0.5",
                        deleted_at=datetime(2020, 1, 1))
            live_a = Host(project_id=proj_id, ip_address="10.0.0.5")
            live_b = Host(project_id=proj_id, ip_address="10.0.0.5")
            only_dead = Host(project_id=proj_id, ip_address="10.0.0.6",
                             deleted_at=datetime(2020, 1, 1))
            db.add_all([dead, live_a, live_b, only_dead])
            await db.flush()
            live_id, dead6_id = live_a.id, only_dead.id
            await db.commit()
        async with maker() as db:
            parent = Execution(item_id=item_id, command="b", status=ExecutionStatus.PENDING)
            db.add(parent)
            await db.commit()
            parent_id = parent.id
        orch, calls = TaskOrchestrator(), []
        with orch_lc.env(maker, tmp_path, stream=_ok_stream(calls)):
            await orch._run_batch_execution(
                execution_id=parent_id, targets=["10.0.0.5", "10.0.0.6"],
                item_id=item_id, base_variables={})
        async with maker() as db:
            kids = (await db.execute(
                select(Execution).where(Execution.id != parent_id))).scalars().all()
            by_cmd = {k.command: k.host_id for k in kids}
            assert by_cmd["scan 10.0.0.5"] == live_id, by_cmd
            assert by_cmd["scan 10.0.0.6"] not in (None, dead6_id), by_cmd  # fresh host
        await engine.dispose()

    asyncio.run(run())


def test_latest_output_wins_and_deleted_executions_ignored(tmp_path):
    async def run():
        engine, maker = await orch_lc.make_maker(tmp_path)
        proj_id, (src_id, dst_id) = await _seed_project(maker, n_items=2)
        async with maker() as db:
            dst = (await db.execute(select(ChecklistItem).where(
                ChecklistItem.id == dst_id))).scalar_one()
            dst.command_template = "echo {val}"
            base = datetime(2024, 1, 1)
            rows = [("old", 0, None), ("new", 1, None), ("gone", 2, datetime(2024, 6, 1))]
            for i, (val, day, deleted) in enumerate(rows):
                ex = Execution(item_id=src_id, command="c", version=i + 1,
                               status=ExecutionStatus.COMPLETED,
                               created_at=base + timedelta(days=day), deleted_at=deleted)
                db.add(ex)
                await db.flush()
                db.add(ExecutionOutput(execution_id=ex.id, key="val", value=val,
                                       data_type="string"))
            await db.commit()
        orch = TaskOrchestrator()
        calls = []
        with orch_lc.env(maker, tmp_path, stream=_ok_stream(calls)):
            async with maker() as db:
                ex = await orch.execute_item(db, item_id=dst_id)
                await db.commit()
        assert ex.command == "echo new", ex.command
        await engine.dispose()

    asyncio.run(run())


def test_next_version_uses_max_without_loading_executions(tmp_path):
    async def run():
        engine, maker = await orch_lc.make_maker(tmp_path)
        item_id, seed_ex = await seed_item(maker)
        await _finish_seed_exec(maker, seed_ex)
        async with maker() as db:
            for v in (2, 5):
                db.add(Execution(item_id=item_id, command="c", version=v,
                                 status=ExecutionStatus.COMPLETED, stdout="x" * 10))
            await db.commit()
        stmts = []

        @sa_event.listens_for(engine.sync_engine, "before_cursor_execute")
        def _spy(conn, cur, statement, *a):
            stmts.append(statement)

        orch = TaskOrchestrator()
        with orch_lc.env(maker, tmp_path):
            async with maker() as db:
                ex = await orch.create_execution_record(db, item_id=item_id)
                await db.commit()
        assert ex.version == 6, ex.version
        assert not any("executions.stdout" in s and "FROM executions" in s for s in stmts), \
            "create_execution_record must not load past executions"
        await engine.dispose()

    asyncio.run(run())


def test_pending_notification_deferred_until_commit(tmp_path):
    async def run():
        engine, maker = await orch_lc.make_maker(tmp_path)
        item_id, seed_ex = await seed_item(maker)
        await _finish_seed_exec(maker, seed_ex)
        orch = TaskOrchestrator()
        with orch_lc.env(maker, tmp_path) as log:
            async with maker() as db:
                ex = await orch.create_execution_record(db, item_id=item_id)
                await asyncio.sleep(0.05)
                assert not log, f"notified while write txn open: {log}"
                await db.commit()
            await asyncio.sleep(0.05)
        assert [(l[0], l[1]) for l in log] == [(ex.id, "pending")], log
        assert log[0][2]["details"] == {"item_name": "I"}
        await engine.dispose()

    asyncio.run(run())
