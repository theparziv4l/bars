"""Finding severity selection and alert severity aliases."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.database import Base
import app.models  # noqa: F401
from app.models.project import Project
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.execution import Execution, ExecutionStatus
from app.models.finding import Finding, FindingSeverity, FindingStatus
from app.core.findings import ingest_execution_findings
from app.core.notifications import AlertSeverity, notification_manager


async def _findings():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        proj = Project(name="P"); db.add(proj); await db.flush()
        grp = ChecklistGroup(project_id=proj.id, name="G"); db.add(grp); await db.flush()
        item = ChecklistItem(group_id=grp.id, name="ms17", command_template="x")
        db.add(item); await db.flush()
        item = (await db.execute(select(ChecklistItem).where(ChecklistItem.id == item.id)
                .options(selectinload(ChecklistItem.group)))).scalar_one()

        async def run(alerts):
            ex = Execution(item_id=item.id, host_id=1, version=1, command="c",
                           status=ExecutionStatus.COMPLETED, alerts_triggered=alerts)
            db.add(ex); await db.flush()
            await ingest_execution_findings(db, ex, item)
            return (await db.execute(select(Finding))).scalars().one()

        # worst alert wins even when a milder one comes first in output order
        f = await run([{"severity": "warning", "matched_text": "Anonymous"},
                       {"severity": "critical", "matched_text": "VULNERABLE"}])
        assert f.severity == FindingSeverity.CRITICAL

        # OPEN finding escalates on a later, worse run (and never lowers)
        f.severity = FindingSeverity.INFO
        await db.flush()
        f = await run([{"severity": "warning", "matched_text": "Anonymous"}])
        assert f.severity == FindingSeverity.MEDIUM
        f = await run([{"severity": "info", "matched_text": "meh"}])
        assert f.severity == FindingSeverity.MEDIUM
        f = await run([{"severity": "critical", "matched_text": "VULNERABLE"}])
        assert f.severity == FindingSeverity.CRITICAL

        # triaged (non-OPEN) finding: operator lowered it, re-run must not touch severity
        f.status = FindingStatus.ACCEPTED
        f.severity = FindingSeverity.LOW
        await db.flush()
        f = await run([{"severity": "critical", "matched_text": "VULNERABLE"}])
        assert f.severity == FindingSeverity.LOW
        assert f.occurrences == 5
    await engine.dispose()


def test_finding_uses_max_severity_and_keeps_user_edit():
    asyncio.run(_findings())


def test_alert_severity_aliases():
    async def go():
        alerts = await notification_manager.process_output_chunk(
            9001, "boom weak info nope",
            {"HIGH": ["boom"], "medium": ["weak"], "Low": ["info"], "bogus": ["nope"]})
        await notification_manager.clear_alerts(9001)
        return alerts

    alerts = asyncio.run(go())
    sev = {a.pattern: a.severity for a in alerts}
    assert sev == {"boom": AlertSeverity.CRITICAL, "weak": AlertSeverity.WARNING,
                   "info": AlertSeverity.INFO, "nope": AlertSeverity.WARNING}
