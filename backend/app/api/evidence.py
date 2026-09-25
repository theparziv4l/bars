"""Evidence API — upload/attach proof (screenshots, files, output excerpts) to findings."""
import asyncio
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, List

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.config import settings
from app.core.auth import get_current_user_optional
from app.models.evidence import Evidence
from app.models.finding import Finding
from app.models.user import User
from app.core.utils import utcnow

router = APIRouter()

MAX_EVIDENCE_BYTES = 25 * 1024 * 1024


class EvidenceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    public_id: str
    project_id: Optional[int]
    finding_id: Optional[int]
    host_id: Optional[int]
    execution_id: Optional[int]
    kind: str
    caption: Optional[str]
    content: Optional[str]
    mime: Optional[str]
    filename: Optional[str]
    created_at: datetime


class TextEvidence(BaseModel):
    content: str
    caption: Optional[str] = None
    kind: str = "output_excerpt"
    project_id: Optional[int] = None
    finding_id: Optional[int] = None
    host_id: Optional[int] = None
    execution_id: Optional[int] = None


def _write_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


async def _resolve_project_id(db: AsyncSession, project_id, finding_id) -> Optional[int]:
    if project_id:
        return project_id
    if finding_id:
        f = (await db.execute(select(Finding).where(Finding.id == finding_id))).scalar_one_or_none()
        return f.project_id if f else None
    return None


@router.post("", response_model=EvidenceResponse, status_code=201)
async def upload_evidence(
    file: UploadFile = File(...),
    caption: Optional[str] = Form(None),
    kind: str = Form("file"),
    project_id: Optional[int] = Form(None),
    finding_id: Optional[int] = Form(None),
    host_id: Optional[int] = Form(None),
    execution_id: Optional[int] = Form(None),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    pid = await _resolve_project_id(db, project_id, finding_id)
    if pid is not None:
        from app.api.projects import _require_project_member
        await _require_project_member(db, pid, current_user)
    safe = Path(file.filename or "evidence").name  # strip any path components
    rel = Path("evidence") / str(pid or "unscoped") / f"{uuid.uuid4().hex}_{safe}"
    dest = settings.STORAGE_PATH / rel
    data = await file.read(MAX_EVIDENCE_BYTES + 1)
    if len(data) > MAX_EVIDENCE_BYTES:
        raise HTTPException(status_code=413, detail="Evidence file too large")
    await asyncio.to_thread(_write_bytes, dest, data)

    ev = Evidence(
        project_id=pid, finding_id=finding_id, host_id=host_id, execution_id=execution_id,
        kind="screenshot" if (file.content_type or "").startswith("image/") else kind,
        caption=caption, local_path=str(dest), mime=file.content_type, filename=safe,
    )
    db.add(ev)
    await db.flush()
    await db.refresh(ev)
    return ev


@router.post("/text", response_model=EvidenceResponse, status_code=201)
async def attach_text_evidence(
    data: TextEvidence,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    pid = await _resolve_project_id(db, data.project_id, data.finding_id)
    if pid is not None:
        from app.api.projects import _require_project_member
        await _require_project_member(db, pid, current_user)
    ev = Evidence(
        project_id=pid, finding_id=data.finding_id, host_id=data.host_id,
        execution_id=data.execution_id, kind=data.kind, caption=data.caption, content=data.content,
    )
    db.add(ev)
    await db.flush()
    await db.refresh(ev)
    return ev


@router.get("", response_model=List[EvidenceResponse])
async def list_evidence(
    finding_id: Optional[int] = None,
    project_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    if project_id is not None:
        from app.api.projects import _require_project_member
        await _require_project_member(db, project_id, current_user)
    stmt = select(Evidence).where(Evidence.deleted_at.is_(None))
    if finding_id is not None:
        stmt = stmt.where(Evidence.finding_id == finding_id)
    if project_id is not None:
        stmt = stmt.where(Evidence.project_id == project_id)
    rows = list((await db.execute(stmt)).scalars().all())
    rows.sort(key=lambda e: e.created_at, reverse=True)
    return rows


@router.get("/{evidence_id}/serve")
async def serve_evidence(
    evidence_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    ev = (await db.execute(select(Evidence).where(Evidence.id == evidence_id))).scalar_one_or_none()
    if not ev or ev.deleted_at is not None or not ev.local_path:
        raise HTTPException(status_code=404, detail="Evidence file not found")
    if ev.project_id is not None:
        from app.api.projects import _require_project_member
        await _require_project_member(db, ev.project_id, current_user)
    base = settings.STORAGE_PATH.resolve()
    path = Path(ev.local_path).resolve()
    if path != base and base not in path.parents:
        raise HTTPException(status_code=404, detail="Evidence file not found")
    if not await asyncio.to_thread(path.exists):
        raise HTTPException(status_code=404, detail="Evidence file missing on disk")
    return FileResponse(path, filename=ev.filename or path.name, media_type=ev.mime or "application/octet-stream")


@router.delete("/{evidence_id}", status_code=204)
async def delete_evidence(
    evidence_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    ev = (await db.execute(select(Evidence).where(Evidence.id == evidence_id))).scalar_one_or_none()
    if ev is not None and ev.project_id is not None:
        from app.api.projects import _require_project_member
        await _require_project_member(db, ev.project_id, current_user)
    if ev and ev.deleted_at is None:
        ev.deleted_at = utcnow()
        await db.flush()
    return None
