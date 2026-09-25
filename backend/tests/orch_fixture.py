"""Shared helpers for orchestrator tests (not collected: no test_* name)."""
import uuid

from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.execution import Execution, ExecutionStatus
from app.models.project import Project


async def seed_item(maker, **kw):
    """Seed project+group+item+PENDING execution. Returns (item_id, execution_id).

    Known kwargs: command_template, output_regex, alert_patterns,
    storage_policy. Anything else is ignored.
    """
    async with maker() as db:
        proj = Project(name=f"P-{uuid.uuid4().hex[:6]}")
        db.add(proj)
        await db.flush()
        grp = ChecklistGroup(project_id=proj.id, name="G")
        db.add(grp)
        await db.flush()
        item = ChecklistItem(
            group_id=grp.id,
            name="I",
            command_template=kw.get("command_template", "echo hi"),
            output_regex=kw.get("output_regex") or {},
            alert_patterns=kw.get("alert_patterns") or {},
            storage_policy=kw.get("storage_policy") or {},
        )
        db.add(item)
        await db.flush()
        ex = Execution(
            public_id=f"ex-{uuid.uuid4().hex[:8]}",
            item_id=item.id,
            command="(pending)",
            status=ExecutionStatus.PENDING,
        )
        db.add(ex)
        await db.commit()
        return item.id, ex.id


def stub_stream(events):
    """Return an async-generator function yielding the given event dicts."""

    async def _gen(*args, **kwargs):
        for e in events:
            yield dict(e)

    return _gen
