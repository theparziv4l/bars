"""A1 Findings: alert matches -> deduped, persisted Finding rows.

Run: `python -m pytest tests/test_findings.py` or `python tests/test_findings.py`.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # backend/

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.database import Base
import app.models  # noqa: F401  (register all models)
from app.models.project import Project
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.execution import Execution, ExecutionStatus
from app.models.finding import Finding, FindingSeverity
from app.core.findings import ingest_execution_findings


async def _run():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    async with Session() as db:
        proj = Project(name="Engagement")
        db.add(proj)
        await db.flush()
        grp = ChecklistGroup(project_id=proj.id, name="AD Enum")
        db.add(grp)
        await db.flush()

        templated = ChecklistItem(
            group_id=grp.id, name="NXC SMB Signing", command_template="nxc smb {target}",
            alert_patterns={"warning": ["signing:False"]},
            finding_template={"title": "SMB Signing Not Required", "severity": "medium",
                              "cwe": "CWE-326", "remediation": "Enforce SMB signing via GPO"},
        )
        fallback = ChecklistItem(
            group_id=grp.id, name="NXC SMB ms17-010", command_template="nxc smb {target} -M ms17-010",
            alert_patterns={"critical": ["VULNERABLE"]},
        )
        db.add_all([templated, fallback])
        await db.flush()

        async def item_with_group(item_id):
            return (await db.execute(
                select(ChecklistItem).where(ChecklistItem.id == item_id)
                .options(selectinload(ChecklistItem.group))
            )).scalar_one()

        def make_exec(item_id, alerts, host_id=1):
            return Execution(item_id=item_id, host_id=host_id, version=1,
                             status=ExecutionStatus.COMPLETED, command="cmd",
                             alerts_triggered=alerts)

        # 1) Templated finding is created with the template's title/severity.
        e1 = make_exec(templated.id, [{"severity": "warning", "matched_text": "signing:False"}])
        db.add(e1); await db.flush()
        assert await ingest_execution_findings(db, e1, await item_with_group(templated.id)) == 1
        f = (await db.execute(select(Finding))).scalars().one()
        assert f.title == "SMB Signing Not Required"
        assert f.severity == FindingSeverity.MEDIUM
        assert f.cwe == "CWE-326" and f.source == "alert"

        # 2) Re-ingest same scope -> dedup (no new row, occurrences bumped).
        e2 = make_exec(templated.id, [{"severity": "warning", "matched_text": "signing:False"}])
        db.add(e2); await db.flush()
        assert await ingest_execution_findings(db, e2, await item_with_group(templated.id)) == 0
        f = (await db.execute(select(Finding).where(Finding.item_id == templated.id))).scalars().one()
        assert f.occurrences == 2

        # 3) Fallback (no template): title=item name, severity from alert bucket.
        e3 = make_exec(fallback.id, [{"severity": "critical", "matched_text": "Host is VULNERABLE"}])
        db.add(e3); await db.flush()
        assert await ingest_execution_findings(db, e3, await item_with_group(fallback.id)) == 1
        f2 = (await db.execute(select(Finding).where(Finding.item_id == fallback.id))).scalars().one()
        assert f2.title == "NXC SMB ms17-010"
        assert f2.severity == FindingSeverity.CRITICAL

        # 4) Different host => a distinct finding (host-scoped dedup).
        e4 = make_exec(templated.id, [{"severity": "warning", "matched_text": "signing:False"}], host_id=2)
        db.add(e4); await db.flush()
        assert await ingest_execution_findings(db, e4, await item_with_group(templated.id)) == 1

        total = len((await db.execute(select(Finding))).scalars().all())
        assert total == 3, f"expected 3 findings, got {total}"

    await engine.dispose()
    print("PASS test_findings: templated + dedup + fallback-severity + host-scoping")


def test_findings_ingest():
    asyncio.run(_run())


if __name__ == "__main__":
    test_findings_ingest()
