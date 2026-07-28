"""Target resolution: an empty variable must not count as a supplied value.

The UI submits the checklist item's declared variable defaults, and the seeded library
ships project variables like ``targets = ""``. Presence-based guards therefore saw a
value that wasn't one and skipped tag resolution / batching, producing commands such as
``nmap -iL `` with a dangling flag.

Run: `python -m pytest tests/test_target_resolution.py` or `python tests/test_target_resolution.py`.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # backend/

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401  (register all models)
from app.models.project import Project
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.execution import Execution, ExecutionStatus
from app.models.host import Host
from app.models.variable import ProjectVariable
from app.core.orchestrator import TaskOrchestrator, task_orchestrator
from app.core.templating import template_engine
from app.core.utils import is_unset, merge_set_values


async def _tag_resolution_survives_empty_targets():
    """Item with target_filter tags + a project variable `targets = ""` still resolves."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    orch = TaskOrchestrator()

    # Never touch the host agent: we only care about the command that gets rendered.
    async def _no_subprocess(execution, context, item, db, timeout, output_callback=None, cwd=None):
        execution.exit_code = 0

    orch._run_subprocess = _no_subprocess

    async with Session() as db:
        project = Project(name="Engagement")
        db.add(project)
        await db.flush()

        db.add_all([
            Host(project_id=project.id, ip_address="10.0.0.5", tags=["smb"]),
            Host(project_id=project.id, ip_address="10.0.0.6", tags=["smb"]),
            Host(project_id=project.id, ip_address="10.0.0.7", tags=["web"]),
            # Seeded library default — present, but carries nothing.
            ProjectVariable(project_id=project.id, key="targets", value="", var_type="file"),
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

        execution = await orch.execute_item(
            db=db,
            item_id=item.id,
            variables={"targets": ""},  # what the Execute modal actually sends
        )

        resolved = execution.variables_used.get("targets")
        assert resolved == ["10.0.0.5", "10.0.0.6"], f"tag resolution failed: {resolved!r}"
        assert "{targets}" not in execution.command, "placeholder left unrendered"
        assert execution.command.strip() != "netexec smb", "command rendered with an empty target list"

    await engine.dispose()


async def _multi_target_still_batches():
    """A multi-IP `target` must still fan out when `targets` is declared but empty."""
    orch = TaskOrchestrator()
    seen = {}

    async def _record_batch(execution_id, targets, item_id, base_variables,
                            flow_execution_id=None, flow_step_index=None, command_override=None):
        seen["targets"] = targets

    orch._run_batch_execution = _record_batch

    await orch.run_execution_background(
        execution_id=1,
        item_id=1,
        variables={"target": "1.1.1.1 2.2.2.2", "targets": ""},
        command_override="nmap {target}",  # no {targets}: iterative batch is the right mode
    )

    assert seen.get("targets") == ["1.1.1.1", "2.2.2.2"], f"batch not split: {seen!r}"


def _unresolved_placeholders_are_reported():
    """An unbound variable stays visible instead of silently vanishing."""
    unresolved = []
    rendered = template_engine.render("nmap -iL {targets}", {}, unresolved=unresolved)
    assert rendered == "nmap -iL {targets}", rendered
    assert unresolved == ["{targets}"], unresolved

    # A supplied value still renders, and reports nothing.
    unresolved = []
    rendered = template_engine.render("nmap {target}", {"target": "10.0.0.5"}, unresolved=unresolved)
    assert rendered == "nmap 10.0.0.5" and unresolved == [], (rendered, unresolved)


def _unset_semantics():
    assert all(is_unset(v) for v in (None, "", [], {}))
    assert not any(is_unset(v) for v in (0, False, "x", [1], {"a": 1}))
    # An empty caller value must not blank a resolved one.
    assert merge_set_values({"target": "10.0.0.5"}, {"target": ""}) == {"target": "10.0.0.5"}
    assert merge_set_values({"target": "10.0.0.5"}, {"target": "10.0.0.9"}) == {"target": "10.0.0.9"}


def test_target_resolution():
    _unset_semantics()
    _unresolved_placeholders_are_reported()
    asyncio.run(_tag_resolution_survives_empty_targets())
    asyncio.run(_multi_target_still_batches())
    print("PASS test_target_resolution: tag resolution + batching + unresolved reporting")


if __name__ == "__main__":
    test_target_resolution()
