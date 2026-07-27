"""Turn execution alert matches into persistent Finding rows.

Bridges the ephemeral alert pipeline (``ChecklistItem.alert_patterns`` ->
``execution.alerts_triggered``) to durable, deduped ``Finding`` records.
"""
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.checklist import ChecklistItem
from app.models.execution import Execution
from app.models.finding import Finding, FindingSeverity, FindingStatus, SEVERITY_ORDER

# Legacy alert buckets -> Finding severities (also accept direct finding severities).
_SEV_MAP = {
    "info": FindingSeverity.INFO,
    "low": FindingSeverity.LOW,
    "warning": FindingSeverity.MEDIUM,
    "medium": FindingSeverity.MEDIUM,
    "high": FindingSeverity.HIGH,
    "critical": FindingSeverity.CRITICAL,
}


def _severity(value) -> FindingSeverity:
    return _SEV_MAP.get(str(value or "").lower(), FindingSeverity.INFO)


async def ingest_execution_findings(db: AsyncSession, execution: Execution, item: ChecklistItem) -> int:
    """Create/update Findings from ``execution.alerts_triggered``. Returns rows created.

    Uses the item's optional ``finding_template`` (title/severity/cwe/remediation/...)
    for a good finding; else falls back to the item name + alert severity bucket.
    Deduped by (project_id, item_id, host_id, title) — repeat hits bump ``occurrences``
    and escalate severity rather than spawning duplicates.
    """
    alerts = execution.alerts_triggered or []
    if not alerts:
        return 0
    project_id = item.group.project_id if item.group else None
    if not project_id:
        return 0

    tmpl = item.finding_template or {}
    tmpl_title = (tmpl.get("title") or "").strip()
    created = 0
    seen: set[str] = set()
    touched: list = []

    for alert in alerts:
        title = tmpl_title or (item.name or "Finding").strip()
        if title in seen:  # collapse repeated matches within one execution
            continue
        seen.add(title)

        severity = _severity(tmpl.get("severity") or alert.get("severity"))
        matched = alert.get("matched_text") or alert.get("context_line") or ""

        existing = (await db.execute(
            select(Finding).where(
                Finding.project_id == project_id,
                Finding.item_id == item.id,
                Finding.host_id == execution.host_id,
                Finding.title == title,
                Finding.deleted_at.is_(None),
            ).limit(1)
        )).scalars().first()

        if existing is not None:
            existing.occurrences = (existing.occurrences or 1) + 1
            existing.execution_id = execution.id
            if matched:
                existing.evidence_text = matched
            existing.updated_at = datetime.utcnow()
            if SEVERITY_ORDER[severity] > SEVERITY_ORDER[existing.severity]:
                existing.severity = severity
            touched.append(existing)
        else:
            new_finding = Finding(
                project_id=project_id,
                title=title,
                description=tmpl.get("description"),
                severity=severity,
                status=FindingStatus.OPEN,
                cwe=tmpl.get("cwe"),
                cve=tmpl.get("cve"),
                cvss_vector=tmpl.get("cvss_vector"),
                cvss_score=tmpl.get("cvss_score"),
                remediation=tmpl.get("remediation"),
                references=tmpl.get("references") or [],
                host_id=execution.host_id,
                item_id=item.id,
                execution_id=execution.id,
                source="alert",
                evidence_text=matched,
            )
            db.add(new_finding)
            touched.append(new_finding)
            created += 1

    # Flush so new findings get their public_id, then record sync events so findings
    # replicate to peers (multi-node). Best-effort — must never break finalize.
    if touched:
        try:
            await db.flush()
            from app.core.sync import sync_service, finding_sync_payload
            for f in touched:
                payload = await finding_sync_payload(db, f)
                await sync_service.record_event(
                    db, entity_type="findings", entity_public_id=f.public_id,
                    operation="update", payload=payload,
                )
        except Exception as e:
            print(f"[Findings] sync record failed: {e}")

    return created
