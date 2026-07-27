"""Checklist API endpoints."""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse
import json
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, inspect as sa_inspect, func
from sqlalchemy.orm import selectinload

from app.core.auth import get_current_user, get_current_user_optional
from app.core.collaboration import (
    ClaimConflictError,
    ProjectAccessError,
    claim_item_scope,
    release_item_scope,
)
from app.core.notifications import notification_manager
from app.core.sync import sync_service
from app.config import settings
from app.database import get_db
from app.api.projects import _require_project_member
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.execution import Execution, ExecutionStatus
from app.models.checklist_claim import ChecklistClaim
from app.models.host import Host
from app.models.project import Project
from app.models.user import ProjectMember, User
from app.schemas.checklist import (
    ChecklistGroupCreate, ChecklistGroupUpdate, ChecklistGroupResponse,
    ChecklistClaimAction,
    ChecklistClaimInfo,
    ChecklistItemCreate, ChecklistItemUpdate, ChecklistItemResponse,
)

router = APIRouter()


async def _get_item_execution_stats(
    db: AsyncSession, item_ids: list[int]
) -> dict[int, dict]:
    """Get execution stats (count, has_successful, latest_*) per item_id via aggregation."""
    if not item_ids:
        return {}

    # execution_count per item
    count_stmt = (
        select(Execution.item_id, func.count(Execution.id).label("cnt"))
        .where(Execution.item_id.in_(item_ids))
        .group_by(Execution.item_id)
    )
    count_rows = (await db.execute(count_stmt)).all()
    counts = {r[0]: r[1] for r in count_rows}

    # item_ids that have at least one successful execution
    success_stmt = (
        select(Execution.item_id)
        .where(
            Execution.item_id.in_(item_ids),
            Execution.status == ExecutionStatus.COMPLETED,
            Execution.exit_code == 0,
        )
        .distinct()
    )
    success_ids = {r[0] for r in (await db.execute(success_stmt)).all()}

    # Latest execution per item (by max version)
    latest_subq = (
        select(
            Execution.item_id,
            func.max(Execution.version).label("max_ver"),
        )
        .where(Execution.item_id.in_(item_ids))
        .group_by(Execution.item_id)
    ).subquery()

    latest_stmt = (
        select(Execution.item_id, Execution.status, Execution.completed_at, Execution.id)
        .join(latest_subq, (Execution.item_id == latest_subq.c.item_id) & (Execution.version == latest_subq.c.max_ver))
    )
    latest_rows = (await db.execute(latest_stmt)).all()
    latest_map = {r[0]: {"status": r[1], "completed_at": r[2], "id": r[3]} for r in latest_rows}

    result = {}
    for iid in item_ids:
        latest = latest_map.get(iid)
        result[iid] = {
            "execution_count": counts.get(iid, 0),
            "has_successful": iid in success_ids,
            "latest_status": latest["status"].value if latest and latest["status"] else None,
            "latest_at": latest["completed_at"] if latest else None,
            "latest_id": latest["id"] if latest else None,
        }
    return result


async def _load_active_claim_lookup(
    db: AsyncSession,
    project_id: Optional[int],
    host_id: Optional[int] = None,
) -> dict[int, ChecklistClaim]:
    """Return active claim map keyed by item_id. When host_id is None (list view),
    load all active claims for the project and prefer project-scope claim per item;
    when host_id is set, filter to that host only."""
    if not project_id:
        return {}

    query = (
        select(ChecklistClaim)
        .where(
            ChecklistClaim.project_id == project_id,
            ChecklistClaim.is_active.is_(True),
        )
        .options(selectinload(ChecklistClaim.claimed_by_user))
    )
    if host_id is not None:
        query = query.where(ChecklistClaim.host_id == host_id)
    # When host_id is None, do not filter by host_id: load both project- and host-scoped claims.

    rows = await db.execute(query)
    claims = rows.scalars().all()
    # One claim per item_id: prefer project-scope (host_id is None) over host-scoped.
    result: dict[int, ChecklistClaim] = {}
    for c in claims:
        if c.item_id not in result:
            result[c.item_id] = c
        else:
            existing = result[c.item_id]
            if c.host_id is None and existing.host_id is not None:
                result[c.item_id] = c
    return result


async def _group_sync_payload(db: AsyncSession, group: ChecklistGroup) -> dict:
    project_public_id = None
    if group.project_id is not None:
        project_result = await db.execute(select(Project).where(Project.id == group.project_id))
        project = project_result.scalar_one_or_none()
        project_public_id = project.public_id if project else None
    return {
        "public_id": group.public_id,
        "project_id": group.project_id,
        "project_public_id": project_public_id,
        "name": group.name,
        "description": group.description,
        "order_index": group.order_index,
        "icon": group.icon,
        "color": group.color,
        "collapsed": group.collapsed,
        "is_template": group.is_template,
        "is_trashed": group.is_trashed,
        "updated_at": group.updated_at.isoformat() if group.updated_at else None,
        "deleted_at": group.deleted_at.isoformat() if group.deleted_at else None,
    }


async def _item_sync_payload(db: AsyncSession, item: ChecklistItem) -> dict:
    group_result = await db.execute(select(ChecklistGroup).where(ChecklistGroup.id == item.group_id))
    group = group_result.scalar_one_or_none()
    project_public_id = None
    if group and group.project_id is not None:
        project_result = await db.execute(select(Project).where(Project.id == group.project_id))
        project = project_result.scalar_one_or_none()
        project_public_id = project.public_id if project else None
    return {
        "public_id": item.public_id,
        "group_id": item.group_id,
        "group_public_id": group.public_id if group else None,
        "project_public_id": project_public_id,
        "name": item.name,
        "description": item.description,
        "command_template": item.command_template,
        "output_regex": item.output_regex,
        "variables": item.variables,
        "input_definitions": item.input_definitions,
        "storage_policy": item.storage_policy,
        "parameter_schema": item.parameter_schema,
        "target_filter": item.target_filter,
        "timeout": item.timeout,
        "alert_patterns": item.alert_patterns,
        "finding_template": item.finding_template,
        "tags": item.tags,
        "enabled": item.enabled,
        "order_index": item.order_index,
        "is_trashed": item.is_trashed,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        "deleted_at": item.deleted_at.isoformat() if item.deleted_at else None,
    }


@router.get("/groups", response_model=List[ChecklistGroupResponse])
async def list_groups(
    project_id: Optional[int] = None,
    is_template: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """List checklist groups for a project or global templates."""
    if current_user and project_id:
        membership = await db.execute(
            select(ProjectMember.id).where(
                ProjectMember.project_id == project_id,
                ProjectMember.user_id == current_user.id,
                ProjectMember.deleted_at.is_(None),
            )
        )
        if membership.scalar_one_or_none() is None:
            raise HTTPException(status_code=403, detail="Project access denied")

    query = select(ChecklistGroup)
    
    if project_id:
        query = query.where(ChecklistGroup.project_id == project_id)
        # Filter out trashed groups for project view
        if not is_template:
            query = query.where(ChecklistGroup.is_trashed == False)
    else:
        # If no project_id, assume we want templates or items with no project
        query = query.where(ChecklistGroup.project_id.is_(None))
        
    if is_template:
        query = query.where(ChecklistGroup.is_template == True)
        
    result = await db.execute(
        query
        .options(selectinload(ChecklistGroup.items))
        .order_by(ChecklistGroup.order_index)
    )
    groups = result.scalars().all()
    claim_lookup = await _load_active_claim_lookup(db, project_id)

    domain_ids = {g.ad_domain_id for g in groups if getattr(g, "ad_domain_id", None)}
    domain_name_map = {}
    if domain_ids:
        from app.models.ad_domain import ADDomain
        domain_result = await db.execute(
            select(ADDomain.id, ADDomain.name).where(ADDomain.id.in_(domain_ids))
        )
        domain_name_map = {r[0]: r[1] for r in domain_result.all()}
    for g in groups:
        g._ad_domain_name = domain_name_map.get(getattr(g, "ad_domain_id", None))

    all_item_ids = [i.id for g in groups for i in (g.items or [])]
    execution_stats = await _get_item_execution_stats(db, all_item_ids)

    response_groups = []
    for g in groups:
        response_group = _group_to_response(
            g, claim_lookup=claim_lookup, execution_stats=execution_stats
        )
        if not is_template:
             # Filter out trashed items logic
             # Since we added is_trashed to the schema, we can check it directly on the response object
             response_group.items = [i for i in response_group.items if not i.is_trashed]
        response_groups.append(response_group)

    return response_groups


@router.post("/groups", response_model=ChecklistGroupResponse, status_code=201)
async def create_group(
    data: ChecklistGroupCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Create a new checklist group."""
    if current_user and data.project_id:
        membership = await db.execute(
            select(ProjectMember.id).where(
                ProjectMember.project_id == data.project_id,
                ProjectMember.user_id == current_user.id,
                ProjectMember.deleted_at.is_(None),
            )
        )
        if membership.scalar_one_or_none() is None:
            raise HTTPException(status_code=403, detail="Project access denied")

    if data.order_index is None:
        from sqlalchemy import func
        count_result = await db.execute(
            select(func.count()).select_from(ChecklistGroup)
            .where(ChecklistGroup.project_id == data.project_id)
        )
        data.order_index = count_result.scalar() or 0
    
    group = ChecklistGroup(**data.model_dump())
    db.add(group)
    await db.flush()
    await db.refresh(group)
    await sync_service.record_event(
        db,
        entity_type="checklist_groups",
        entity_public_id=group.public_id,
        operation="create",
        payload=await _group_sync_payload(db, group),
    )
    return _group_to_response(group)


@router.put("/groups/{group_id}", response_model=ChecklistGroupResponse)
async def update_group(
    group_id: int,
    data: ChecklistGroupUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Update a checklist group."""
    result = await db.execute(
        select(ChecklistGroup).where(ChecklistGroup.id == group_id).options(
            selectinload(ChecklistGroup.items)
        )
    )
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    if current_user and group.project_id:
        membership = await db.execute(
            select(ProjectMember.id).where(
                ProjectMember.project_id == group.project_id,
                ProjectMember.user_id == current_user.id,
                ProjectMember.deleted_at.is_(None),
            )
        )
        if membership.scalar_one_or_none() is None:
            raise HTTPException(status_code=403, detail="Project access denied")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(group, field, value)
    await db.flush()
    await sync_service.record_event(
        db,
        entity_type="checklist_groups",
        entity_public_id=group.public_id,
        operation="update",
        payload=await _group_sync_payload(db, group),
    )
    item_ids = [i.id for i in (group.items or [])]
    execution_stats = await _get_item_execution_stats(db, item_ids)
    claim_lookup = await _load_active_claim_lookup(db, group.project_id)
    return _group_to_response(group, claim_lookup=claim_lookup, execution_stats=execution_stats)


@router.delete("/groups/{group_id}", status_code=204)
async def delete_group(
    group_id: int, 
    soft_delete: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Delete a checklist group."""
    result = await db.execute(select(ChecklistGroup).where(ChecklistGroup.id == group_id))
    group = result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    if current_user and group.project_id:
        membership = await db.execute(
            select(ProjectMember.id).where(
                ProjectMember.project_id == group.project_id,
                ProjectMember.user_id == current_user.id,
                ProjectMember.deleted_at.is_(None),
            )
        )
        if membership.scalar_one_or_none() is None:
            raise HTTPException(status_code=403, detail="Project access denied")
        
    if soft_delete:
        group.is_trashed = True
        # Also soft delete items? 
        # Usually yes, but technically if group is hidden, items are hidden.
        # Let's mark them too for consistency if we query items directly.
        # However, for now, hiding group is enough or we can cascade soft delete.
        # Let's keep it simple: soft delete group hides it.
        # If we want to support "restore", we need to know if item was trashed individually or via group.
        # For this requirement "delete item results must be deleted" on hard, "item results kept" on soft.
        # If we soft delete group, we just hide it. Results remain.
        await sync_service.record_event(
            db,
            entity_type="checklist_groups",
            entity_public_id=group.public_id,
            operation="update",
            payload=await _group_sync_payload(db, group),
        )
    else:
        group.deleted_at = datetime.utcnow()
        await sync_service.record_event(
            db,
            entity_type="checklist_groups",
            entity_public_id=group.public_id,
            operation="delete",
            payload=await _group_sync_payload(db, group),
        )
        await db.delete(group)
    
    await db.commit()


@router.post("/import", status_code=201)
async def import_checklist(
    project_id: int,
    template_group_ids: List[int],
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
    ad_domain_id: Optional[int] = None,
):
    """Import checklist templates into a project."""
    await _require_project_member(db, project_id, current_user)
    # Get current max order index
    from sqlalchemy import func
    count_result = await db.execute(
        select(func.count()).select_from(ChecklistGroup)
        .where(ChecklistGroup.project_id == project_id)
    )
    current_count = count_result.scalar() or 0
    
    result = await db.execute(
        select(ChecklistGroup)
        .where(ChecklistGroup.id.in_(template_group_ids))
        .where(ChecklistGroup.project_id.is_(None))
        .options(selectinload(ChecklistGroup.items))
    )
    templates = result.scalars().all()
    
    imported = []
    for i, tmpl in enumerate(templates):
        group_name = tmpl.name
        if ad_domain_id:
            from app.models.ad_domain import ADDomain
            domain_result = await db.execute(
                select(ADDomain).where(ADDomain.id == ad_domain_id)
            )
            ad_domain = domain_result.scalar_one_or_none()
            if ad_domain:
                group_name = f"{tmpl.name} — {ad_domain.name}"

        new_group = ChecklistGroup(
            project_id=project_id,
            name=group_name,
            description=tmpl.description,
            icon=tmpl.icon,
            color=tmpl.color,
            order_index=current_count + i,
            is_template=False,
            ad_domain_id=ad_domain_id,
        )
        db.add(new_group)
        await db.flush()

        new_items = []
        for item in tmpl.items:
            new_item = ChecklistItem(
                group_id=new_group.id,
                name=item.name,
                description=item.description,
                command_template=item.command_template,
                output_regex=item.output_regex,
                variables=item.variables,
                order_index=item.order_index,
                enabled=item.enabled,
                timeout=item.timeout,
                alert_patterns=item.alert_patterns,
                finding_template=getattr(item, "finding_template", None) or {},
                tags=item.tags,
                parameter_schema=getattr(item, "parameter_schema", None) or {},
                target_filter=getattr(item, "target_filter", None) or {},
            )
            db.add(new_item)
            new_items.append(new_item)

        await db.flush()
        await sync_service.record_event(
            db,
            entity_type="checklist_groups",
            entity_public_id=new_group.public_id,
            operation="create",
            payload=await _group_sync_payload(db, new_group),
        )
        for new_item in new_items:
            await sync_service.record_event(
                db,
                entity_type="checklist_items",
                entity_public_id=new_item.public_id,
                operation="create",
                payload=await _item_sync_payload(db, new_item),
            )
        imported.append(new_group)

    await db.commit()
    return {"status": "imported", "count": len(imported)}


@router.post("/import/all", status_code=201)
async def import_all_checklists(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
    ad_domain_id: Optional[int] = None,
):
    """Import ALL global templates into a project."""
    await _require_project_member(db, project_id, current_user)
    # Get all global template groups
    result = await db.execute(
        select(ChecklistGroup)
        .where(ChecklistGroup.project_id.is_(None))
        .where(ChecklistGroup.is_template == True)
    )
    templates = result.scalars().all()
    
    if not templates:
        return {"status": "no_templates", "count": 0}
        
    # Reuse import logic
    template_ids = [t.id for t in templates]
    return await import_checklist(project_id, template_ids, db, current_user, ad_domain_id)



@router.get("/items", response_model=List[ChecklistItemResponse])
async def list_items(
    group_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """List checklist items in a group."""
    group_result = await db.execute(select(ChecklistGroup).where(ChecklistGroup.id == group_id))
    group = group_result.scalar_one_or_none()
    if not group:
        raise HTTPException(status_code=404, detail="Group not found")
    if current_user and group.project_id:
        membership = await db.execute(
            select(ProjectMember.id).where(
                ProjectMember.project_id == group.project_id,
                ProjectMember.user_id == current_user.id,
                ProjectMember.deleted_at.is_(None),
            )
        )
        if membership.scalar_one_or_none() is None:
            raise HTTPException(status_code=403, detail="Project access denied")

    claim_lookup = await _load_active_claim_lookup(db, group.project_id if group else None)
    result = await db.execute(
        select(ChecklistItem)
        .where(ChecklistItem.group_id == group_id)
        .where(ChecklistItem.is_trashed == False)
        .order_by(ChecklistItem.order_index)
    )
    items = result.scalars().all()
    execution_stats = await _get_item_execution_stats(db, [i.id for i in items])
    return [
        _item_to_response(i, claim_lookup=claim_lookup, execution_stats=execution_stats)
        for i in items
    ]


@router.post("/items", response_model=ChecklistItemResponse, status_code=201)
async def create_item(
    data: ChecklistItemCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Create a new checklist item."""
    if current_user:
        group_result = await db.execute(select(ChecklistGroup).where(ChecklistGroup.id == data.group_id))
        group = group_result.scalar_one_or_none()
        if group and group.project_id:
            membership = await db.execute(
                select(ProjectMember.id).where(
                    ProjectMember.project_id == group.project_id,
                    ProjectMember.user_id == current_user.id,
                    ProjectMember.deleted_at.is_(None),
                )
            )
            if membership.scalar_one_or_none() is None:
                raise HTTPException(status_code=403, detail="Project access denied")

    if data.order_index is None:
        from sqlalchemy import func
        count_result = await db.execute(
            select(func.count()).select_from(ChecklistItem)
            .where(ChecklistItem.group_id == data.group_id)
        )
        data.order_index = count_result.scalar() or 0
    
    item = ChecklistItem(**data.model_dump())
    db.add(item)
    await db.flush()
    await db.refresh(item)
    await sync_service.record_event(
        db,
        entity_type="checklist_items",
        entity_public_id=item.public_id,
        operation="create",
        payload=await _item_sync_payload(db, item),
    )
    return _item_to_response(item)


@router.get("/items/{item_id}", response_model=ChecklistItemResponse)
async def get_item(
    item_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Get a checklist item."""
    result = await db.execute(select(ChecklistItem).where(ChecklistItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    group_result = await db.execute(
        select(ChecklistGroup).where(ChecklistGroup.id == item.group_id)
    )
    group = group_result.scalar_one_or_none()
    if current_user and group and group.project_id:
        membership = await db.execute(
            select(ProjectMember.id).where(
                ProjectMember.project_id == group.project_id,
                ProjectMember.user_id == current_user.id,
                ProjectMember.deleted_at.is_(None),
            )
        )
        if membership.scalar_one_or_none() is None:
            raise HTTPException(status_code=403, detail="Project access denied")

    claim_lookup = await _load_active_claim_lookup(db, group.project_id if group else None)
    execution_stats = await _get_item_execution_stats(db, [item.id])
    return _item_to_response(
        item, claim_lookup=claim_lookup, execution_stats=execution_stats
    )


@router.put("/items/{item_id}", response_model=ChecklistItemResponse)
async def update_item(
    item_id: int,
    data: ChecklistItemUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Update a checklist item."""
    result = await db.execute(select(ChecklistItem).where(ChecklistItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if current_user:
        group_result = await db.execute(
            select(ChecklistGroup).where(ChecklistGroup.id == item.group_id)
        )
        group = group_result.scalar_one_or_none()
        if group and group.project_id:
            membership = await db.execute(
                select(ProjectMember.id).where(
                    ProjectMember.project_id == group.project_id,
                    ProjectMember.user_id == current_user.id,
                    ProjectMember.deleted_at.is_(None),
                )
            )
            if membership.scalar_one_or_none() is None:
                raise HTTPException(status_code=403, detail="Project access denied")

    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(item, field, value)
    await db.flush()
    await sync_service.record_event(
        db,
        entity_type="checklist_items",
        entity_public_id=item.public_id,
        operation="update",
        payload=await _item_sync_payload(db, item),
    )
    execution_stats = await _get_item_execution_stats(db, [item.id])
    return _item_to_response(item, execution_stats=execution_stats)


@router.delete("/items/{item_id}", status_code=204)
async def delete_item(
    item_id: int,
    soft_delete: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Delete a checklist item."""
    result = await db.execute(select(ChecklistItem).where(ChecklistItem.id == item_id))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    if current_user:
        group_result = await db.execute(select(ChecklistGroup).where(ChecklistGroup.id == item.group_id))
        group = group_result.scalar_one_or_none()
        if group and group.project_id:
            membership = await db.execute(
                select(ProjectMember.id).where(
                    ProjectMember.project_id == group.project_id,
                    ProjectMember.user_id == current_user.id,
                    ProjectMember.deleted_at.is_(None),
                )
            )
            if membership.scalar_one_or_none() is None:
                raise HTTPException(status_code=403, detail="Project access denied")
        
    if soft_delete:
        item.is_trashed = True
        await sync_service.record_event(
            db,
            entity_type="checklist_items",
            entity_public_id=item.public_id,
            operation="update",
            payload=await _item_sync_payload(db, item),
        )
    else:
        item.deleted_at = datetime.utcnow()
        await sync_service.record_event(
            db,
            entity_type="checklist_items",
            entity_public_id=item.public_id,
            operation="delete",
            payload=await _item_sync_payload(db, item),
        )
        await db.delete(item)
        
    await db.commit()


def _claim_to_info(claim: Optional[ChecklistClaim]) -> ChecklistClaimInfo:
    if claim is None:
        return ChecklistClaimInfo(is_active=False)
    return ChecklistClaimInfo(
        claim_id=claim.id,
        claimed_by_user_id=claim.claimed_by_user_id,
        claimed_by_username=claim.claimed_by_user.username if claim.claimed_by_user else None,
        host_id=claim.host_id,
        claimed_at=claim.claimed_at,
        lease_expires_at=claim.lease_expires_at,
        is_active=claim.is_active,
    )


async def _claim_sync_payload(db: AsyncSession, claim: ChecklistClaim) -> dict:
    project_public_id = None
    item_public_id = None
    host_public_id = None
    user_public_id = None

    project_result = await db.execute(select(Project).where(Project.id == claim.project_id))
    project = project_result.scalar_one_or_none()
    if project:
        project_public_id = project.public_id

    item_result = await db.execute(select(ChecklistItem).where(ChecklistItem.id == claim.item_id))
    item = item_result.scalar_one_or_none()
    if item:
        item_public_id = item.public_id

    if claim.host_id is not None:
        host_result = await db.execute(select(Host).where(Host.id == claim.host_id))
        host = host_result.scalar_one_or_none()
        if host:
            host_public_id = host.public_id

    if claim.claimed_by_user_id is not None:
        user_result = await db.execute(select(User).where(User.id == claim.claimed_by_user_id))
        c_user = user_result.scalar_one_or_none()
        if c_user:
            user_public_id = c_user.public_id

    return {
        "public_id": claim.public_id,
        "project_id": claim.project_id,
        "project_public_id": project_public_id,
        "item_id": claim.item_id,
        "item_public_id": item_public_id,
        "host_id": claim.host_id,
        "host_public_id": host_public_id,
        "host_scope_key": claim.host_scope_key,
        "claimed_by_user_id": claim.claimed_by_user_id,
        "claimed_by_user_public_id": user_public_id,
        "claimed_at": claim.claimed_at.isoformat() if claim.claimed_at else None,
        "lease_expires_at": claim.lease_expires_at.isoformat() if claim.lease_expires_at else None,
        "is_active": claim.is_active,
        "version": claim.version,
        "updated_at": claim.updated_at.isoformat() if claim.updated_at else None,
        "deleted_at": claim.deleted_at.isoformat() if claim.deleted_at else None,
    }


@router.post("/items/{item_id}/claim", response_model=ChecklistClaimInfo)
async def claim_item(
    item_id: int,
    data: ChecklistClaimAction,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Claim an item scope for collaborative execution."""
    if not settings.CLAIMS_ENABLED:
        raise HTTPException(status_code=503, detail="Checklist claims are disabled")
    try:
        claim = await claim_item_scope(
            db,
            item_id=item_id,
            host_id=data.host_id,
            user=current_user,
            lease_seconds=data.lease_seconds,
            force_takeover=False,
        )
    except ProjectAccessError as err:
        raise HTTPException(status_code=403, detail=str(err)) from err
    except ClaimConflictError as err:
        current = err.claim
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "claimed_by_other",
                "item_id": item_id,
                "host_id": current.host_id,
                "claimed_by_user_id": current.claimed_by_user_id,
                "claimed_by_username": current.claimed_by_user.username if current.claimed_by_user else None,
                "lease_expires_at": current.lease_expires_at.isoformat() if current.lease_expires_at else None,
            },
        ) from err

    await notification_manager.notify(
        "item_claimed",
        {
            "project_id": claim.project_id,
            "claim_id": claim.id,
            "item_id": claim.item_id,
            "host_id": claim.host_id,
            "claimed_by_user_id": claim.claimed_by_user_id,
            "claimed_by_username": claim.claimed_by_user.username if claim.claimed_by_user else None,
            "lease_expires_at": claim.lease_expires_at.isoformat() if claim.lease_expires_at else None,
        },
    )
    return _claim_to_info(claim)


@router.post("/items/{item_id}/takeover", response_model=ChecklistClaimInfo)
async def takeover_item(
    item_id: int,
    data: ChecklistClaimAction,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Force-take ownership of an item scope."""
    if not settings.CLAIMS_ENABLED:
        raise HTTPException(status_code=503, detail="Checklist claims are disabled")
    try:
        claim = await claim_item_scope(
            db,
            item_id=item_id,
            host_id=data.host_id,
            user=current_user,
            lease_seconds=data.lease_seconds,
            force_takeover=True,
        )
    except ProjectAccessError as err:
        raise HTTPException(status_code=403, detail=str(err)) from err
    await notification_manager.notify(
        "item_taken_over",
        {
            "project_id": claim.project_id,
            "claim_id": claim.id,
            "item_id": claim.item_id,
            "host_id": claim.host_id,
            "claimed_by_user_id": claim.claimed_by_user_id,
            "claimed_by_username": claim.claimed_by_user.username if claim.claimed_by_user else None,
            "lease_expires_at": claim.lease_expires_at.isoformat() if claim.lease_expires_at else None,
        },
    )
    return _claim_to_info(claim)


@router.post("/items/{item_id}/release", response_model=ChecklistClaimInfo)
async def release_item(
    item_id: int,
    data: ChecklistClaimAction,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Release ownership of an item scope."""
    if not settings.CLAIMS_ENABLED:
        raise HTTPException(status_code=503, detail="Checklist claims are disabled")
    try:
        claim = await release_item_scope(
            db,
            item_id=item_id,
            host_id=data.host_id,
            user=current_user,
            force=False,
        )
    except ProjectAccessError as err:
        raise HTTPException(status_code=403, detail=str(err)) from err
    except ClaimConflictError as err:
        current = err.claim
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "claimed_by_other",
                "item_id": item_id,
                "host_id": current.host_id,
                "claimed_by_user_id": current.claimed_by_user_id,
                "claimed_by_username": current.claimed_by_user.username if current.claimed_by_user else None,
            },
        ) from err

    await notification_manager.notify(
        "item_released",
        {
            "project_id": claim.project_id if claim else None,
            "item_id": item_id,
            "host_id": data.host_id,
            "released_by_user_id": current_user.id,
            "released_by_username": current_user.username,
        },
    )
    return _claim_to_info(claim)


def _group_to_response(
    group: ChecklistGroup,
    claim_lookup: Optional[dict[int, ChecklistClaim]] = None,
    execution_stats: Optional[dict[int, dict]] = None,
) -> ChecklistGroupResponse:
    items_loaded = "items" not in sa_inspect(group).unloaded
    items = group.items if items_loaded else []
    execution_stats = execution_stats or {}
    response_items = [
        _item_to_response(i, claim_lookup=claim_lookup, execution_stats=execution_stats)
        for i in (items or [])
    ]
    # Exclude trashed items from progress so the percentage matches the visible list
    # (and the project-level stats, which already exclude trashed items).
    active_items = [i for i in response_items if not getattr(i, "is_trashed", False)]
    total = len(active_items)
    completed = sum(1 for i in active_items if i.has_successful_execution)
    completion_stats = {
        "total": total,
        "completed": completed,
        "percentage": round((completed / total) * 100, 1) if total else 0,
    }
    ad_domain_id = getattr(group, "ad_domain_id", None)
    ad_domain_name = getattr(group, "_ad_domain_name", None)
    return ChecklistGroupResponse(
        id=group.id,
        project_id=group.project_id,
        name=group.name,
        description=group.description,
        order_index=group.order_index,
        icon=group.icon,
        color=group.color,
        collapsed=group.collapsed,
        is_template=group.is_template,
        is_default_import=group.is_default_import,
        requires_auth=group.requires_auth,
        speed_profile=group.speed_profile,
        created_at=group.created_at,
        ad_domain_id=ad_domain_id,
        ad_domain_name=ad_domain_name,
        items=response_items,
        completion_stats=completion_stats,
    )


def _item_to_response(
    item: ChecklistItem,
    claim_lookup: Optional[dict[int, ChecklistClaim]] = None,
    execution_stats: Optional[dict[int, dict]] = None,
) -> ChecklistItemResponse:
    stats = (execution_stats or {}).get(item.id)
    if stats is not None:
        has_successful = stats["has_successful"]
        execution_count = stats["execution_count"]
        latest_status = stats["latest_status"]
        latest_at = stats["latest_at"]
        latest_id = stats["latest_id"]
    else:
        latest = item.latest_execution
        execs_loaded = "executions" not in sa_inspect(item).unloaded
        has_successful = item.has_successful_execution
        execution_count = len(item.executions) if execs_loaded and item.executions else 0
        latest_status = latest.status.value if latest else None
        latest_at = latest.completed_at if latest else None
        latest_id = latest.id if latest else None

    claim = (claim_lookup or {}).get(item.id)
    return ChecklistItemResponse(
        id=item.id,
        group_id=item.group_id,
        name=item.name,
        description=item.description,
        command_template=item.command_template,
        output_regex=item.output_regex,
        variables=item.variables,
        input_definitions=item.input_definitions,
        storage_policy=item.storage_policy,
        parameter_schema=getattr(item, "parameter_schema", None) or {},
        target_filter=getattr(item, "target_filter", None) or {},
        timeout=item.timeout,
        alert_patterns=item.alert_patterns,
        tags=item.tags,
        enabled=item.enabled,
        order_index=item.order_index,
        created_at=item.created_at,
        updated_at=item.updated_at,
        has_successful_execution=has_successful,
        execution_count=execution_count,
        latest_execution_status=latest_status,
        latest_execution_at=latest_at,
        latest_execution_id=latest_id,
        is_trashed=item.is_trashed,
        claim=ChecklistClaimInfo(
            claim_id=claim.id,
            claimed_by_user_id=claim.claimed_by_user_id,
            claimed_by_username=claim.claimed_by_user.username if claim and claim.claimed_by_user else None,
            host_id=claim.host_id,
            claimed_at=claim.claimed_at,
            lease_expires_at=claim.lease_expires_at,
            is_active=claim.is_active,
        )
        if claim
        else None,
    )

@router.get("/export/json")
async def export_checklists_json(
    project_id: Optional[int] = None,
    group_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db)
):
    """Export checklists in JSON format."""
    query = select(ChecklistGroup).options(selectinload(ChecklistGroup.items))
    
    if group_id:
        query = query.where(ChecklistGroup.id == group_id)
    elif project_id:
        query = query.where(ChecklistGroup.project_id == project_id)
    else:
        # Export global templates
        query = query.where(ChecklistGroup.project_id.is_(None))
        
    result = await db.execute(query)
    groups = result.scalars().all()
    
    export_data = {
        "groups": []
    }
    
    for group in groups:
        group_data = {
            "name": group.name,
            "description": group.description,
            "is_default_import": group.is_default_import,
            "requires_auth": group.requires_auth,
            "speed_profile": group.speed_profile,
            "items": []
        }
        
        for item in group.items:
            item_data = {
                "name": item.name,
                "description": item.description,
                "command_template": item.command_template,
                "output_regex": item.output_regex,
                "input_definitions": item.input_definitions,
                "storage_policy": item.storage_policy,
                "parameter_schema": item.parameter_schema,
                "timeout": item.timeout,
                "alert_patterns": item.alert_patterns,
                "finding_template": item.finding_template,
                "tags": item.tags,
                "enabled": item.enabled
            }
            group_data["items"].append(item_data)
            
        export_data["groups"].append(group_data)
        
    return JSONResponse(
        content=export_data,
        headers={"Content-Disposition": f"attachment; filename=checklists_export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"}
    )


@router.post("/import/file")
async def import_checklists_file(
    project_id: Optional[int] = None,
    ad_domain_id: Optional[int] = None,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Import checklists from a JSON file. If project_id is None, import as global templates."""
    if project_id is not None:
        await _require_project_member(db, project_id, current_user)
    if not file.filename.endswith('.json'):
        raise HTTPException(status_code=400, detail="Invalid file format. Please upload a JSON file.")
        
    try:
        content = await file.read()
        data = json.loads(content)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON content.")
        
    if "groups" not in data:
        raise HTTPException(status_code=400, detail="Invalid export file format.")
        
    try:
        # Get current max order index
        from sqlalchemy import func as sa_func
        count_q = select(sa_func.count()).select_from(ChecklistGroup)
        if project_id:
            count_q = count_q.where(ChecklistGroup.project_id == project_id)
        else:
            count_q = count_q.where(ChecklistGroup.project_id.is_(None))
            
        current_count = (await db.execute(count_q)).scalar() or 0
        
        imported_count = 0
        
        for i, group_data in enumerate(data["groups"]):
            group_name = group_data["name"]
            if ad_domain_id and project_id:
                from app.models.ad_domain import ADDomain
                domain_result = await db.execute(
                    select(ADDomain).where(ADDomain.id == ad_domain_id)
                )
                ad_domain = domain_result.scalar_one_or_none()
                if ad_domain:
                    group_name = f"{group_data['name']} — {ad_domain.name}"

            new_group = ChecklistGroup(
                project_id=project_id,
                name=group_name,
                description=group_data.get("description"),
                icon=group_data.get("icon"),
                color=group_data.get("color"),
                order_index=current_count + i,
                is_template=True if project_id is None else False,
                collapsed=group_data.get("collapsed", False),
                ad_domain_id=ad_domain_id if project_id else None,
                is_default_import=group_data.get("is_default_import", False) if project_id is None else False,
                requires_auth=group_data.get("requires_auth", False) if project_id is None else False,
                speed_profile=group_data.get("speed_profile", "default"),
            )
            db.add(new_group)
            await db.flush()

            new_items = []
            items_list = group_data.get("items", [])
            for j, item_data in enumerate(items_list):
                output_regex = item_data.get("output_regex")
                if output_regex is None: output_regex = {}

                variables = item_data.get("variables")
                if variables is None: variables = {}

                input_definitions = item_data.get("input_definitions")
                if input_definitions is None: input_definitions = {}

                storage_policy = item_data.get("storage_policy")
                if storage_policy is None: storage_policy = {}

                parameter_schema = item_data.get("parameter_schema")
                if parameter_schema is None: parameter_schema = {}

                target_filter = item_data.get("target_filter")
                if target_filter is None: target_filter = {}

                alert_patterns = item_data.get("alert_patterns")
                if alert_patterns is None: alert_patterns = {}

                tags = item_data.get("tags")
                if tags is None: tags = []

                new_item = ChecklistItem(
                    group_id=new_group.id,
                    name=item_data["name"],
                    description=item_data.get("description"),
                    command_template=item_data.get("command_template", ""),
                    output_regex=output_regex,
                    variables=variables,
                    input_definitions=input_definitions,
                    storage_policy=storage_policy,
                    parameter_schema=parameter_schema,
                    target_filter=target_filter,
                    timeout=item_data.get("timeout", 3600),
                    alert_patterns=alert_patterns,
                    finding_template=item_data.get("finding_template") or {},
                    tags=tags,
                    enabled=item_data.get("enabled", True),
                    order_index=j
                )
                db.add(new_item)
                new_items.append(new_item)

            await db.flush()
            await sync_service.record_event(
                db,
                entity_type="checklist_groups",
                entity_public_id=new_group.public_id,
                operation="create",
                payload=await _group_sync_payload(db, new_group),
            )
            for new_item in new_items:
                await sync_service.record_event(
                    db,
                    entity_type="checklist_items",
                    entity_public_id=new_item.public_id,
                    operation="create",
                    payload=await _item_sync_payload(db, new_item),
                )
            imported_count += 1

        await db.commit()
        return {"status": "imported", "count": imported_count}
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Import failed: {str(e)}")
