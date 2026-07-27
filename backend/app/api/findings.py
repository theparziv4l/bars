"""Findings / vulnerability API — CRUD + severity rollup."""
from datetime import datetime
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.core.sync import sync_service, finding_sync_payload
from app.models.finding import Finding, FindingSeverity, FindingStatus, SEVERITY_ORDER

router = APIRouter()


async def _record(db: AsyncSession, finding: Finding, operation: str) -> None:
    """Record a sync event so the finding replicates to peers. Best-effort."""
    try:
        payload = await finding_sync_payload(db, finding)
        await sync_service.record_event(
            db, entity_type="findings", entity_public_id=finding.public_id,
            operation=operation, payload=payload,
        )
    except Exception as e:  # noqa: BLE001
        print(f"[Findings] sync record failed: {e}")


class FindingCreate(BaseModel):
    project_id: int
    title: str = Field(..., min_length=1, max_length=500)
    description: Optional[str] = None
    severity: FindingSeverity = FindingSeverity.INFO
    status: FindingStatus = FindingStatus.OPEN
    cvss_vector: Optional[str] = None
    cvss_score: Optional[float] = None
    cwe: Optional[str] = None
    cve: Optional[str] = None
    remediation: Optional[str] = None
    notes: Optional[str] = None
    references: List[str] = Field(default_factory=list)
    host_id: Optional[int] = None
    service_id: Optional[int] = None


class FindingUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=500)
    description: Optional[str] = None
    severity: Optional[FindingSeverity] = None
    status: Optional[FindingStatus] = None
    cvss_vector: Optional[str] = None
    cvss_score: Optional[float] = None
    cwe: Optional[str] = None
    cve: Optional[str] = None
    remediation: Optional[str] = None
    notes: Optional[str] = None
    references: Optional[List[str]] = None
    host_id: Optional[int] = None
    service_id: Optional[int] = None


class FindingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    public_id: str
    project_id: int
    title: str
    description: Optional[str]
    severity: FindingSeverity
    status: FindingStatus
    cvss_vector: Optional[str]
    cvss_score: Optional[float]
    cwe: Optional[str]
    cve: Optional[str]
    remediation: Optional[str]
    notes: Optional[str]
    references: List[str]
    host_id: Optional[int]
    service_id: Optional[int]
    item_id: Optional[int]
    execution_id: Optional[int]
    source: str
    evidence_text: Optional[str]
    occurrences: int
    discovered_at: datetime
    updated_at: datetime


async def _get(db: AsyncSession, finding_id: int) -> Finding:
    finding = (await db.execute(select(Finding).where(Finding.id == finding_id))).scalar_one_or_none()
    if not finding or finding.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Finding not found")
    return finding


@router.get("", response_model=List[FindingResponse])
async def list_findings(
    project_id: int,
    severity: Optional[FindingSeverity] = None,
    status: Optional[FindingStatus] = None,
    host_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Finding).where(Finding.project_id == project_id, Finding.deleted_at.is_(None))
    if severity is not None:
        stmt = stmt.where(Finding.severity == severity)
    if status is not None:
        stmt = stmt.where(Finding.status == status)
    if host_id is not None:
        stmt = stmt.where(Finding.host_id == host_id)
    rows = list((await db.execute(stmt)).scalars().all())
    rows.sort(key=lambda f: (SEVERITY_ORDER.get(f.severity, 0), f.updated_at), reverse=True)
    return rows


@router.get("/summary")
async def findings_summary(project_id: int, db: AsyncSession = Depends(get_db)):
    """Severity rollup (excludes false-positives). Feeds the dashboard + report exec summary."""
    stmt = (
        select(Finding.severity, func.count())
        .where(
            Finding.project_id == project_id,
            Finding.deleted_at.is_(None),
            Finding.status != FindingStatus.FALSE_POSITIVE,
        )
        .group_by(Finding.severity)
    )
    counts = {s.value: 0 for s in FindingSeverity}
    for sev, n in (await db.execute(stmt)).all():
        key = sev.value if hasattr(sev, "value") else str(sev)
        counts[key] = n
    counts["total"] = sum(counts[s.value] for s in FindingSeverity)
    return counts


@router.post("", response_model=FindingResponse, status_code=201)
async def create_finding(data: FindingCreate, db: AsyncSession = Depends(get_db)):
    finding = Finding(**data.model_dump(), source="manual")
    db.add(finding)
    await db.flush()
    await db.refresh(finding)
    await _record(db, finding, "create")
    return finding


@router.get("/{finding_id}", response_model=FindingResponse)
async def get_finding(finding_id: int, db: AsyncSession = Depends(get_db)):
    return await _get(db, finding_id)


@router.put("/{finding_id}", response_model=FindingResponse)
async def update_finding(finding_id: int, data: FindingUpdate, db: AsyncSession = Depends(get_db)):
    finding = await _get(db, finding_id)
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(finding, field, value)
    finding.updated_at = datetime.utcnow()
    await db.flush()
    await db.refresh(finding)
    await _record(db, finding, "update")
    return finding


@router.delete("/{finding_id}", status_code=204)
async def delete_finding(finding_id: int, db: AsyncSession = Depends(get_db)):
    finding = (await db.execute(select(Finding).where(Finding.id == finding_id))).scalar_one_or_none()
    if finding and finding.deleted_at is None:
        finding.deleted_at = datetime.utcnow()
        await db.flush()
        await _record(db, finding, "delete")
    return None
