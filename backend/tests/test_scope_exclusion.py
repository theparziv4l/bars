"""Excluded hosts are out of scope: no execution path may target them.

An ROE carve-out is only worth anything if it holds on the server. These cover the
chokepoints a target can enter through — tag resolution, the operator's target list,
a host-scoped run, and flow auto-resolution — rather than the UI that hides them.

Run: `python -m pytest tests/test_scope_exclusion.py` or `python tests/test_scope_exclusion.py`.
"""
import asyncio
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # backend/

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

import app.database as app_database
from app.database import Base
import app.models  # noqa: F401  (register all models)
from app.models.project import Project
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.execution import Execution, ExecutionStatus
from app.models.flow import Flow
from app.models.host import Host
from app.core.flow_manager import FlowManager
from app.core.orchestrator import TaskOrchestrator
from app.core.utils import as_target_list, drop_excluded, excluded_targets


async def _fresh_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def _silent_orchestrator():
    """An orchestrator that renders commands but never shells out."""
    orch = TaskOrchestrator()

    async def _no_subprocess(execution, context, item, db, timeout, output_callback=None, cwd=None):
        execution.exit_code = 0

    orch._run_subprocess = _no_subprocess
    return orch


async def _tag_resolution_skips_excluded():
    """{targets} resolved from host tags must not include an excluded host."""
    engine, Session = await _fresh_db()
    orch = _silent_orchestrator()

    async with Session() as db:
        project = Project(name="Engagement")
        db.add(project)
        await db.flush()

        db.add_all([
            Host(project_id=project.id, ip_address="10.0.0.5", tags=["smb"]),
            # Same tag, but the client carved it out.
            Host(project_id=project.id, ip_address="10.0.0.6", tags=["smb"], excluded=True),
        ])

        group = ChecklistGroup(project_id=project.id, name="AD Enum")
        db.add(group)
        await db.flush()
        item = ChecklistItem(
            group_id=group.id,
            name="NXC SMB signing",
            command_template="netexec smb {targets}",
            target_filter={"tags": ["smb"]},
        )
        db.add(item)
        await db.flush()

        execution = await orch.execute_item(db=db, item_id=item.id, variables={})

        resolved = execution.variables_used.get("targets")
        assert resolved == ["10.0.0.5"], f"excluded host leaked into targets: {resolved!r}"
        assert "10.0.0.6" not in execution.command, execution.command

    await engine.dispose()


async def _operator_target_list_is_filtered():
    """An excluded IP the operator sent anyway is dropped before the batch fans out."""
    engine, Session = await _fresh_db()
    orch = _silent_orchestrator()
    seen = {}

    async def _record_batch(execution_id, targets, item_id, base_variables,
                            flow_execution_id=None, flow_step_index=None, command_override=None):
        seen["targets"] = targets

    orch._run_batch_execution = _record_batch

    original_maker = app_database.async_session_maker
    try:
        async with Session() as db:
            project = Project(name="Engagement")
            db.add(project)
            await db.flush()
            db.add_all([
                Host(project_id=project.id, ip_address="10.0.0.5"),
                Host(project_id=project.id, ip_address="10.0.0.6", excluded=True),
                Host(project_id=project.id, ip_address="10.0.0.7"),
            ])
            group = ChecklistGroup(project_id=project.id, name="Recon")
            db.add(group)
            await db.flush()
            item = ChecklistItem(group_id=group.id, name="Ping", command_template="nmap {target}")
            db.add(item)
            await db.flush()
            item_id = item.id
            await db.commit()

        app_database.async_session_maker = Session
        await orch.run_execution_background(
            execution_id=1,
            item_id=item_id,
            variables={"target": "10.0.0.5 10.0.0.6 10.0.0.7"},
        )
    finally:
        app_database.async_session_maker = original_maker

    assert seen.get("targets") == ["10.0.0.5", "10.0.0.7"], f"excluded IP survived: {seen!r}"

    await engine.dispose()


async def _all_excluded_refuses_to_run():
    """When every requested target is out of scope, nothing runs — the execution fails."""
    engine, Session = await _fresh_db()
    orch = _silent_orchestrator()
    launched = {"batch": False}

    async def _record_batch(*args, **kwargs):
        launched["batch"] = True

    orch._run_batch_execution = _record_batch

    original_maker = app_database.async_session_maker
    try:
        async with Session() as db:
            project = Project(name="Engagement")
            db.add(project)
            await db.flush()
            db.add_all([
                Host(project_id=project.id, ip_address="10.0.0.5", excluded=True),
                Host(project_id=project.id, ip_address="10.0.0.6", excluded=True),
            ])
            group = ChecklistGroup(project_id=project.id, name="Recon")
            db.add(group)
            await db.flush()
            item = ChecklistItem(group_id=group.id, name="Ping", command_template="nmap {target}")
            db.add(item)
            await db.flush()
            execution = Execution(
                item_id=item.id, version=1, status=ExecutionStatus.PENDING, command="(pending)"
            )
            db.add(execution)
            await db.flush()
            item_id, execution_id = item.id, execution.id
            await db.commit()

        app_database.async_session_maker = Session
        await orch.run_execution_background(
            execution_id=execution_id,
            item_id=item_id,
            variables={"target": "10.0.0.5 10.0.0.6"},
        )

        async with Session() as db:
            refreshed = await db.get(Execution, execution_id)
            assert refreshed.status == ExecutionStatus.FAILED, refreshed.status
            assert "excluded from scope" in (refreshed.stderr or ""), refreshed.stderr
    finally:
        app_database.async_session_maker = original_maker

    assert not launched["batch"], "a command was launched against an all-excluded target list"

    await engine.dispose()


async def _host_scoped_run_is_refused():
    """A run pinned to one excluded host has no target list to filter — block it too."""
    engine, Session = await _fresh_db()
    orch = _silent_orchestrator()

    original_maker = app_database.async_session_maker
    try:
        async with Session() as db:
            project = Project(name="Engagement")
            db.add(project)
            await db.flush()
            host = Host(project_id=project.id, ip_address="10.0.0.9", excluded=True)
            db.add(host)
            group = ChecklistGroup(project_id=project.id, name="Recon")
            db.add(group)
            await db.flush()
            item = ChecklistItem(group_id=group.id, name="Enum", command_template="nmap {target}")
            db.add(item)
            await db.flush()
            execution = Execution(
                item_id=item.id, host_id=host.id, version=1,
                status=ExecutionStatus.PENDING, command="(pending)",
            )
            db.add(execution)
            await db.flush()
            item_id, host_id, execution_id = item.id, host.id, execution.id
            await db.commit()

        app_database.async_session_maker = Session
        await orch.run_execution_background(
            execution_id=execution_id, item_id=item_id, host_id=host_id, variables={},
        )

        async with Session() as db:
            refreshed = await db.get(Execution, execution_id)
            assert refreshed.status == ExecutionStatus.FAILED, refreshed.status
            assert "excluded from scope" in (refreshed.stderr or ""), refreshed.stderr
    finally:
        app_database.async_session_maker = original_maker

    await engine.dispose()


async def _flow_auto_resolve_skips_excluded():
    """A flow with no explicit targets pulls project hosts — minus the excluded ones."""
    engine, Session = await _fresh_db()

    async with Session() as db:
        project = Project(name="Engagement")
        db.add(project)
        await db.flush()
        db.add_all([
            Host(project_id=project.id, ip_address="10.0.0.5"),
            Host(project_id=project.id, ip_address="10.0.0.6", excluded=True),
            # Soft-deleted hosts were already out; keep them out.
            Host(project_id=project.id, ip_address="10.0.0.7", deleted_at=datetime.utcnow()),
        ])
        flow = Flow(project_id=project.id, name="Recon flow")
        db.add(flow)
        await db.flush()

        targets = await FlowManager()._auto_resolve_targets(db, flow)
        assert targets == ["10.0.0.5"], f"flow auto-resolve leaked a host: {targets!r}"

    await engine.dispose()


async def _excluded_targets_matches_names_not_just_ips():
    """Targets arrive as display names too — the filter has to catch hostname/fqdn."""
    engine, Session = await _fresh_db()

    async with Session() as db:
        project = Project(name="Engagement")
        db.add(project)
        await db.flush()
        db.add(Host(
            project_id=project.id, ip_address="10.0.0.6",
            hostname="DC01", fqdn="dc01.corp.local", excluded=True,
        ))
        db.add(Host(project_id=project.id, ip_address="10.0.0.5", hostname="WS01"))
        await db.flush()

        excluded = await excluded_targets(db, project.id)
        assert excluded == {"10.0.0.6", "DC01", "dc01.corp.local"}, excluded
        assert drop_excluded("10.0.0.5 DC01 dc01.corp.local", excluded) == ["10.0.0.5"]
        assert drop_excluded(["WS01", "10.0.0.6"], excluded) == ["WS01"]

        # An unknown project filters nothing rather than blowing up.
        assert await excluded_targets(db, None) == set()

    await engine.dispose()


async def _export_never_carries_excluded_ips():
    """The IP export is selection-driven, so it has to drop excluded hosts itself.

    Excluded hosts stay selectable in Assets (that is how re-including them works), so
    "export what I selected" would otherwise hand an out-of-scope IP straight to -iL.
    The empty-selection case is the nastier one: `[]` is truthy in JS and falsy in
    Python, so a naive guard turns "nothing selected" into "export the whole project".
    """
    from httpx import ASGITransport, AsyncClient

    import app.core.host_runner as host_runner_module
    from app.database import get_db
    from app.main import app as fastapi_app

    engine, Session = await _fresh_db()
    sent = []

    async def _fake_agent(command, cwd=None, timeout=60):
        sent.append(command)
        return {"exit_code": 0, "stdout": "", "stderr": ""}

    real_agent = host_runner_module.host_runner.execute_sync
    host_runner_module.host_runner.execute_sync = _fake_agent

    async def _override_db():
        async with Session() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    fastapi_app.dependency_overrides[get_db] = _override_db
    try:
        async with Session() as db:
            project = Project(name="Acme")
            db.add(project)
            await db.flush()
            hosts = [Host(project_id=project.id, ip_address=f"10.0.0.{i}") for i in (5, 6, 7, 8)]
            db.add_all(hosts)
            await db.flush()
            project_id, ids = project.id, [h.id for h in hosts]
            await db.commit()

        transport = ASGITransport(app=fastapi_app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            r = await client.post(
                "/api/v1/hosts/bulk-scope", json={"host_ids": ids[2:], "excluded": True}
            )
            assert r.status_code == 200 and r.json()["updated"] == 2, r.text

            # Selecting everything, excluded rows included, exports only what is in scope.
            r = await client.post(
                f"/api/v1/projects/{project_id}/export-ips", json={"host_ids": ids}
            )
            assert r.status_code == 200 and r.json()["count"] == 2, r.text
            assert "10.0.0.5" in sent[-1] and "10.0.0.6" in sent[-1], sent[-1]
            assert "10.0.0.7" not in sent[-1] and "10.0.0.8" not in sent[-1], sent[-1]

            # A selection of only excluded hosts is refused, not quietly empty.
            r = await client.post(
                f"/api/v1/projects/{project_id}/export-ips", json={"host_ids": ids[2:]}
            )
            assert r.status_code == 400, r.text

            # An explicit empty selection must never mean "the whole project".
            before = len(sent)
            r = await client.post(
                f"/api/v1/projects/{project_id}/export-ips", json={"host_ids": []}
            )
            assert r.status_code == 400, r.text
            assert len(sent) == before, "empty selection wrote a file"

            # An absent key still means every in-scope host.
            r = await client.post(f"/api/v1/projects/{project_id}/export-ips", json={})
            assert r.status_code == 200 and r.json()["count"] == 2, r.text
    finally:
        fastapi_app.dependency_overrides.pop(get_db, None)
        host_runner_module.host_runner.execute_sync = real_agent

    await engine.dispose()


def _target_list_shapes():
    assert as_target_list("1.1.1.1 2.2.2.2") == ["1.1.1.1", "2.2.2.2"]
    assert as_target_list(["1.1.1.1"]) == ["1.1.1.1"]
    assert as_target_list("1.1.1.1") == ["1.1.1.1"]
    assert as_target_list("") == [] and as_target_list(None) == []


def test_scope_exclusion():
    _target_list_shapes()
    asyncio.run(_excluded_targets_matches_names_not_just_ips())
    asyncio.run(_tag_resolution_skips_excluded())
    asyncio.run(_operator_target_list_is_filtered())
    asyncio.run(_all_excluded_refuses_to_run())
    asyncio.run(_host_scoped_run_is_refused())
    asyncio.run(_flow_auto_resolve_skips_excluded())
    asyncio.run(_export_never_carries_excluded_ips())
    print("PASS test_scope_exclusion: tag/target/host/flow/export paths all drop excluded hosts")


if __name__ == "__main__":
    test_scope_exclusion()
