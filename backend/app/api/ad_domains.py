"""AD Domain management API."""
from datetime import datetime
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.ad_domain import ADDomain
from app.models.user import User
from app.core.sync import sync_service

router = APIRouter()


async def _get_current_user_optional(db=None):
    return None

try:
    from app.api.auth import get_current_user_optional
except ImportError:
    get_current_user_optional = _get_current_user_optional

try:
    from app.api.projects import _require_project_member
except ImportError:
    async def _require_project_member(db, project_id, user):
        pass


def _domain_to_dict(d: ADDomain) -> dict:
    return {
        "id": d.id,
        "public_id": d.public_id,
        "project_id": d.project_id,
        "name": d.name,
        "netbios_name": d.netbios_name,
        "dc_ip": d.dc_ip,
        "dc_fqdn": d.dc_fqdn,
        "description": d.description,
        "trust_type": d.trust_type,
        "parent_domain_id": d.parent_domain_id,
        "extra_data": d.extra_data or {},
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }


@router.get("")
async def list_ad_domains(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """List all AD domains for a project."""
    await _require_project_member(db, project_id, current_user)
    result = await db.execute(
        select(ADDomain)
        .where(ADDomain.project_id == project_id, ADDomain.deleted_at.is_(None))
        .order_by(ADDomain.created_at)
    )
    return [_domain_to_dict(d) for d in result.scalars().all()]


@router.get("/{domain_id}")
async def get_ad_domain(
    domain_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Get a single AD domain."""
    result = await db.execute(
        select(ADDomain).where(ADDomain.id == domain_id, ADDomain.deleted_at.is_(None))
    )
    domain = result.scalar_one_or_none()
    if not domain:
        raise HTTPException(status_code=404, detail="AD domain not found")
    await _require_project_member(db, domain.project_id, current_user)
    return _domain_to_dict(domain)


@router.post("", status_code=201)
async def create_ad_domain(
    data: dict,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Create a new AD domain for a project."""
    project_id = data.get("project_id")
    if not project_id:
        raise HTTPException(status_code=400, detail="project_id is required")
    await _require_project_member(db, project_id, current_user)

    domain = ADDomain(
        project_id=project_id,
        name=data["name"],
        netbios_name=data.get("netbios_name"),
        dc_ip=data.get("dc_ip"),
        dc_fqdn=data.get("dc_fqdn"),
        description=data.get("description"),
        trust_type=data.get("trust_type"),
        parent_domain_id=data.get("parent_domain_id"),
        extra_data=data.get("extra_data", {}),
    )
    db.add(domain)
    await db.flush()

    await sync_service.record_event(
        db,
        entity_type="ad_domains",
        entity_public_id=domain.public_id,
        operation="create",
        payload=_domain_to_dict(domain),
    )

    # Auto-import domain-based default templates for the new domain
    from app.core.project_setup import auto_import_for_domain
    try:
        await auto_import_for_domain(db, project_id, domain.id)
    except Exception as e:
        print(f"[ADDomain] Auto-import for domain failed: {e}")

    await db.commit()
    return _domain_to_dict(domain)


@router.put("/{domain_id}")
async def update_ad_domain(
    domain_id: int,
    data: dict,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Update an AD domain."""
    result = await db.execute(
        select(ADDomain).where(ADDomain.id == domain_id, ADDomain.deleted_at.is_(None))
    )
    domain = result.scalar_one_or_none()
    if not domain:
        raise HTTPException(status_code=404, detail="AD domain not found")
    await _require_project_member(db, domain.project_id, current_user)

    for field in ("name", "netbios_name", "dc_ip", "dc_fqdn", "description", "trust_type", "parent_domain_id", "extra_data"):
        if field in data:
            setattr(domain, field, data[field])

    await db.flush()
    await sync_service.record_event(
        db,
        entity_type="ad_domains",
        entity_public_id=domain.public_id,
        operation="update",
        payload=_domain_to_dict(domain),
    )
    await db.commit()
    return _domain_to_dict(domain)


@router.delete("/{domain_id}", status_code=204)
async def delete_ad_domain(
    domain_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Delete an AD domain (sets variables/groups ad_domain_id to NULL)."""
    result = await db.execute(
        select(ADDomain).where(ADDomain.id == domain_id, ADDomain.deleted_at.is_(None))
    )
    domain = result.scalar_one_or_none()
    if not domain:
        raise HTTPException(status_code=404, detail="AD domain not found")
    await _require_project_member(db, domain.project_id, current_user)

    domain.deleted_at = datetime.utcnow()
    await sync_service.record_event(
        db,
        entity_type="ad_domains",
        entity_public_id=domain.public_id,
        operation="delete",
        payload={"public_id": domain.public_id},
    )

    from app.models.variable import ProjectVariable
    from app.models.checklist import ChecklistGroup
    from sqlalchemy import update
    await db.execute(
        update(ProjectVariable).where(ProjectVariable.ad_domain_id == domain_id).values(ad_domain_id=None)
    )
    await db.execute(
        update(ChecklistGroup).where(ChecklistGroup.ad_domain_id == domain_id).values(ad_domain_id=None)
    )

    await db.commit()
