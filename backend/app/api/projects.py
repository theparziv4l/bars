"""Project API endpoints."""
import json
from datetime import datetime
from typing import Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, inspect as sa_inspect, text
from sqlalchemy.orm import selectinload

from app.core.auth import get_current_user, get_current_user_optional
from app.core.sync import sync_service
from app.database import get_db
from app.models.project import Project, ProjectStatus
from app.models.host import Host, Service
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.execution import Execution, ExecutionStatus
from app.models.user import ProjectMember, User
from app.schemas.project import ProjectCreate, ProjectUpdate, ProjectResponse, ProjectListResponse
from app.schemas.host import GenerateScriptScanRequest, GenerateScriptScanResponse
from app.schemas.auth import ProjectMemberCreate, ProjectMemberResponse
from app.core import script_scanner

router = APIRouter()

# Host tags endpoint is at GET /{project_id}/host-tags (see below)


async def _require_project_member(
    db: AsyncSession,
    project_id: int,
    user: Optional[User],
) -> None:
    """Ensure authenticated user belongs to project when auth is enabled."""
    if user is None:
        return

    result = await db.execute(
        select(ProjectMember.id).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user.id,
            ProjectMember.deleted_at.is_(None),
        )
    )
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=403, detail="Project access denied")


async def _get_project_counts(
    db: AsyncSession,
    project_ids: List[int],
) -> Dict[int, dict]:
    """Fetch host_count, member_count, total_items, completed_items for given project IDs."""
    if not project_ids:
        return {}

    # Host counts
    host_rows = await db.execute(
        select(Host.project_id, func.count(Host.id).label("cnt"))
        .where(Host.project_id.in_(project_ids))
        .group_by(Host.project_id)
    )
    host_map = {r[0]: r[1] for r in host_rows.all()}

    # Member counts (active only)
    member_rows = await db.execute(
        select(ProjectMember.project_id, func.count(ProjectMember.id).label("cnt"))
        .where(
            ProjectMember.project_id.in_(project_ids),
            ProjectMember.deleted_at.is_(None),
        )
        .group_by(ProjectMember.project_id)
    )
    member_map = {r[0]: r[1] for r in member_rows.all()}

    # Total items per project (non-trashed items in non-deleted groups)
    total_rows = await db.execute(
        select(ChecklistGroup.project_id, func.count(ChecklistItem.id).label("cnt"))
        .join(ChecklistItem, ChecklistItem.group_id == ChecklistGroup.id)
        .where(
            ChecklistGroup.project_id.in_(project_ids),
            ChecklistGroup.deleted_at.is_(None),
            ChecklistItem.is_trashed.is_(False),
        )
        .group_by(ChecklistGroup.project_id)
    )
    total_map = {r[0]: r[1] for r in total_rows.all()}

    # Completed items: items with at least one successful execution
    completed_subq = (
        select(ChecklistItem.id)
        .join(ChecklistGroup, ChecklistItem.group_id == ChecklistGroup.id)
        .join(Execution, Execution.item_id == ChecklistItem.id)
        .where(
            ChecklistGroup.project_id.in_(project_ids),
            ChecklistGroup.deleted_at.is_(None),
            ChecklistItem.is_trashed.is_(False),
            Execution.status == ExecutionStatus.COMPLETED,
            Execution.exit_code == 0,
        )
    ).distinct()

    completed_per_item = await db.execute(completed_subq)
    completed_item_ids = {r[0] for r in completed_per_item.all()}

    # Map item_id -> project_id to count completed per project
    item_to_project = await db.execute(
        select(ChecklistItem.id, ChecklistGroup.project_id)
        .join(ChecklistGroup, ChecklistItem.group_id == ChecklistGroup.id)
        .where(
            ChecklistGroup.project_id.in_(project_ids),
            ChecklistGroup.deleted_at.is_(None),
            ChecklistItem.is_trashed.is_(False),
        )
    )
    completed_map: Dict[int, int] = {pid: 0 for pid in project_ids}
    for item_id, project_id in item_to_project.all():
        if item_id in completed_item_ids:
            completed_map[project_id] = completed_map.get(project_id, 0) + 1

    result = {}
    for pid in project_ids:
        total = total_map.get(pid, 0)
        completed = completed_map.get(pid, 0)
        progress = (completed / total * 100) if total > 0 else 0
        result[pid] = {
            "host_count": host_map.get(pid, 0),
            "member_count": member_map.get(pid, 0),
            "total_items": total,
            "completed_items": completed,
            "checklist_progress": round(progress, 1),
        }
    return result


async def _project_variable_sync_payload(db: AsyncSession, variable) -> dict:
    project_public_id = None
    host_public_id = None
    proj_result = await db.execute(select(Project).where(Project.id == variable.project_id))
    project = proj_result.scalar_one_or_none()
    if project:
        project_public_id = project.public_id
    if variable.host_id:
        host_result = await db.execute(select(Host).where(Host.id == variable.host_id))
        host = host_result.scalar_one_or_none()
        if host:
            host_public_id = host.public_id
    # value must always be present (including None/"") so empty variables sync correctly
    return {
        "public_id": variable.public_id,
        "project_id": variable.project_id,
        "project_public_id": project_public_id,
        "host_id": variable.host_id,
        "host_public_id": host_public_id,
        "key": variable.key,
        "value": variable.value,
        "var_type": variable.var_type,
        "updated_at": variable.updated_at.isoformat() if variable.updated_at else None,
        "deleted_at": variable.deleted_at.isoformat() if variable.deleted_at else None,
    }


@router.get("", response_model=ProjectListResponse)
async def list_projects(
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    status: Optional[str] = None,
    search: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """List all projects with pagination."""
    query = select(Project).where(Project.deleted_at.is_(None))

    if status:
        query = query.where(Project.status == ProjectStatus(status))
    if search:
        query = query.where(Project.name.ilike(f"%{search}%"))
    if current_user:
        query = query.join(
            ProjectMember,
            ProjectMember.project_id == Project.id,
        ).where(
            ProjectMember.user_id == current_user.id,
            ProjectMember.deleted_at.is_(None),
        )
    
    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar()
    
    # Get page without loading heavy relationships
    query = query.order_by(Project.created_at.desc())
    query = query.offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(query)
    projects = result.scalars().unique().all()

    project_ids = [p.id for p in projects]
    counts_map = await _get_project_counts(db, project_ids)

    return ProjectListResponse(
        items=[_project_to_response(p, counts=counts_map.get(p.id)) for p in projects],
        total=total,
        page=page,
        per_page=per_page,
        pages=(total + per_page - 1) // per_page
    )


@router.post("", response_model=ProjectResponse, status_code=201)
async def create_project(
    data: ProjectCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Create a new project."""
    from app.core.utils import get_project_path, parse_network_interfaces
    from app.core.host_runner import host_runner
    
    # 1. Determine Project Path
    project_path = get_project_path(data.name)
    
    # 2. Create Directory on Host (Sync)
    try:
        # We use strict=False or just ignore if it exists? mkdir -p is safe
        # Command: mkdir -p "{project_path}"
        result = await host_runner.execute_sync(f'mkdir -p "{project_path}"')
        if result["exit_code"] != 0:
            # warn but don't fail project creation? 
            # If we fail here, user can't create project.
            # Let's log it.
            print(f"Failed to create project directory: {result['stderr']}")
    except Exception as e:
        print(f"Error creating project directory: {e}")

    project = Project(
        name=data.name,
        description=data.description,
        scope=data.scope.model_dump() if data.scope else {},
        start_date=data.start_date,
        end_date=data.end_date,
        extra_data={**data.extra_data, "project_path": project_path},  # Store path
        created_by_user_id=current_user.id if current_user else None,
    )
    db.add(project)
    await db.flush()
    await db.refresh(project)

    if current_user:
        db.add(ProjectMember(project_id=project.id, user_id=current_user.id))
    
    # Auto-create start-date variable
    from app.models.variable import ProjectVariable
    start_date = data.start_date.isoformat() if data.start_date else project.created_at.isoformat()
    start_var = ProjectVariable(project_id=project.id, key="start-date", value=start_date)
    db.add(start_var)
    await db.flush()
    await sync_service.record_event(
        db,
        entity_type="project_variables",
        entity_public_id=start_var.public_id,
        operation="create",
        payload=await _project_variable_sync_payload(db, start_var),
    )

    # Auto-create attacker-ips variable (from Host)
    import json
    attacker_ips = {}
    try:
        # parsed output from ip -j addr
        ip_result = await host_runner.execute_sync("ip -j addr")
        if ip_result["exit_code"] == 0:
            ip_data = json.loads(ip_result["stdout"])
            attacker_ips = parse_network_interfaces(ip_data)
        else:
             print(f"Failed to get host IPs: {ip_result['stderr']}")
    except Exception as e:
        print(f"Error getting host IPs: {e}")

    if attacker_ips:
        ips_var = ProjectVariable(project_id=project.id, key="attacker-ips", value=json.dumps(attacker_ips), var_type="json")
        db.add(ips_var)
        await db.flush()
        await sync_service.record_event(
            db,
            entity_type="project_variables",
            entity_public_id=ips_var.public_id,
            operation="create",
            payload=await _project_variable_sync_payload(db, ips_var),
        )

    # Create AD domains from request
    from app.models.ad_domain import ADDomain
    from app.core.project_setup import auto_import_for_project
    domain_ids = []
    for domain_name in (data.domains or []):
        domain_name = domain_name.strip()
        if not domain_name:
            continue
        ad_domain = ADDomain(project_id=project.id, name=domain_name)
        db.add(ad_domain)
        await db.flush()
        domain_ids.append(ad_domain.id)
        await sync_service.record_event(
            db,
            entity_type="ad_domains",
            entity_public_id=ad_domain.public_id,
            operation="create",
            payload={
                "public_id": ad_domain.public_id,
                "project_id": project.id,
                "name": ad_domain.name,
            },
        )

    # Auto-import default library templates
    try:
        await auto_import_for_project(db, project.id, domain_ids)
    except Exception as e:
        print(f"[ProjectSetup] Auto-import failed: {e}")

    await sync_service.record_event(
        db,
        entity_type="projects",
        entity_public_id=project.public_id,
        operation="create",
        payload={
            "public_id": project.public_id,
            "name": project.name,
            "description": project.description,
            "scope": project.scope,
            "status": project.status.value,
            "extra_data": project.extra_data,
            "start_date": project.start_date.isoformat() if project.start_date else None,
            "end_date": project.end_date.isoformat() if project.end_date else None,
            "created_by_user_id": project.created_by_user_id,
            "created_by_user_public_id": current_user.public_id if current_user else None,
            "updated_at": project.updated_at.isoformat() if project.updated_at else None,
            "deleted_at": project.deleted_at.isoformat() if project.deleted_at else None,
        },
    )
    counts_map = await _get_project_counts(db, [project.id])
    return _project_to_response(project, counts=counts_map.get(project.id))


@router.post("/migrate-orphans")
async def migrate_orphan_projects(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Back-fill ProjectMember rows for projects created before collaboration.

    Projects that have no active members are invisible in the project list
    because list_projects joins on project_members. This endpoint adds the
    original creator (or the calling user) as a member for each orphan project.
    """
    # Find project IDs that already have at least one active member
    member_sub = (
        select(ProjectMember.project_id)
        .where(ProjectMember.deleted_at.is_(None))
    )
    orphans_q = (
        select(Project)
        .where(
            Project.deleted_at.is_(None),
            ~Project.id.in_(member_sub),
        )
    )
    result = await db.execute(orphans_q)
    orphan_projects = result.scalars().all()

    migrated = []
    skipped = []

    for project in orphan_projects:
        owner_id = project.created_by_user_id or current_user.id

        # Verify user exists and is active
        user_check = await db.execute(
            select(User.id).where(User.id == owner_id, User.is_active.is_(True))
        )
        if user_check.scalar_one_or_none() is None:
            skipped.append({"id": project.id, "name": project.name, "reason": f"user {owner_id} not found"})
            continue

        member = ProjectMember(project_id=project.id, user_id=owner_id)
        db.add(member)
        await db.flush()
        await db.refresh(member)

        # Look up public IDs for sync payload
        owner_result = await db.execute(select(User.public_id).where(User.id == owner_id))
        owner_public_id = owner_result.scalar_one_or_none()

        await sync_service.record_event(
            db,
            entity_type="project_members",
            entity_public_id=member.public_id,
            operation="create",
            payload={
                "public_id": member.public_id,
                "project_id": member.project_id,
                "project_public_id": project.public_id,
                "user_id": member.user_id,
                "user_public_id": owner_public_id,
                "deleted_at": None,
                "updated_at": member.updated_at.isoformat() if member.updated_at else None,
            },
        )
        migrated.append({"id": project.id, "name": project.name, "assigned_to_user_id": owner_id})

    return {
        "migrated_count": len(migrated),
        "skipped_count": len(skipped),
        "migrated": migrated,
        "skipped": skipped,
    }


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Get a project by ID."""
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.deleted_at.is_(None))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    await _require_project_member(db, project_id, current_user)
    counts_map = await _get_project_counts(db, [project_id])
    return _project_to_response(project, counts=counts_map.get(project_id))


@router.put("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: int,
    data: ProjectUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Update a project."""
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.deleted_at.is_(None))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    await _require_project_member(db, project_id, current_user)
    
    for field, value in data.model_dump(exclude_unset=True).items():
        if field == "scope" and value:
            setattr(project, field, value.model_dump() if hasattr(value, 'model_dump') else value)
        elif field == "status" and value:
            setattr(project, field, ProjectStatus(value))
        else:
            setattr(project, field, value)
    
    await db.flush()
    await db.refresh(project)
    counts_map = await _get_project_counts(db, [project_id])
    await sync_service.record_event(
        db,
        entity_type="projects",
        entity_public_id=project.public_id,
        operation="update",
        payload={
            "public_id": project.public_id,
            "name": project.name,
            "description": project.description,
            "scope": project.scope,
            "status": project.status.value,
            "extra_data": project.extra_data,
            "start_date": project.start_date.isoformat() if project.start_date else None,
            "end_date": project.end_date.isoformat() if project.end_date else None,
            "created_by_user_id": project.created_by_user_id,
            "updated_at": project.updated_at.isoformat() if project.updated_at else None,
            "deleted_at": project.deleted_at.isoformat() if project.deleted_at else None,
        },
    )
    return _project_to_response(project, counts=counts_map.get(project_id))


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Delete a project (soft-delete: sets deleted_at, keeps row for sync consistency)."""
    result = await db.execute(
        select(Project).where(Project.id == project_id, Project.deleted_at.is_(None))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    await _require_project_member(db, project_id, current_user)
    project.deleted_at = datetime.utcnow()
    await sync_service.record_event(
        db,
        entity_type="projects",
        entity_public_id=project.public_id,
        operation="delete",
        payload={
            "public_id": project.public_id,
            "name": project.name,
            "description": project.description,
            "scope": project.scope,
            "status": project.status.value,
            "extra_data": project.extra_data,
            "start_date": project.start_date.isoformat() if project.start_date else None,
            "end_date": project.end_date.isoformat() if project.end_date else None,
            "created_by_user_id": project.created_by_user_id,
            "updated_at": project.updated_at.isoformat() if project.updated_at else None,
            "deleted_at": project.deleted_at.isoformat() if project.deleted_at else None,
        },
    )


@router.get("/{project_id}/members", response_model=List[ProjectMemberResponse])
async def list_project_members(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """List project members."""
    await _require_project_member(db, project_id, current_user)
    result = await db.execute(
        select(ProjectMember)
        .where(
            ProjectMember.project_id == project_id,
            ProjectMember.deleted_at.is_(None),
        )
        .options(selectinload(ProjectMember.user))
        .order_by(ProjectMember.created_at.asc())
    )
    members = result.scalars().all()
    return [_member_to_response(m) for m in members]


@router.post("/{project_id}/members", response_model=ProjectMemberResponse, status_code=201)
async def add_project_member(
    project_id: int,
    data: ProjectMemberCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Add a user to project membership."""
    await _require_project_member(db, project_id, current_user)

    project_exists = await db.execute(select(Project).where(Project.id == project_id))
    project = project_exists.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    key = data.username_or_email.strip()
    user_result = await db.execute(
        select(User).where(
            (User.username == key) | (User.email == key.lower()),
            User.is_active.is_(True),
        )
    )
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    existing_result = await db.execute(
        select(ProjectMember)
        .where(ProjectMember.project_id == project_id, ProjectMember.user_id == user.id)
        .options(selectinload(ProjectMember.user))
    )
    existing = existing_result.scalar_one_or_none()
    if existing:
        existing.deleted_at = None
        await db.flush()
        await sync_service.record_event(
            db,
            entity_type="project_members",
            entity_public_id=existing.public_id,
            operation="upsert",
            payload={
                "public_id": existing.public_id,
                "project_id": existing.project_id,
                "project_public_id": project.public_id,
                "user_id": existing.user_id,
                "user_public_id": user.public_id,
                "deleted_at": None,
                "updated_at": existing.updated_at.isoformat() if existing.updated_at else None,
            },
        )
        return _member_to_response(existing)

    member = ProjectMember(project_id=project_id, user_id=user.id)
    db.add(member)
    await db.flush()
    await db.refresh(member)
    await db.refresh(member, ["user"])
    await sync_service.record_event(
        db,
        entity_type="project_members",
        entity_public_id=member.public_id,
        operation="create",
        payload={
            "public_id": member.public_id,
            "project_id": member.project_id,
            "project_public_id": project.public_id,
            "user_id": member.user_id,
            "user_public_id": user.public_id,
            "deleted_at": member.deleted_at.isoformat() if member.deleted_at else None,
            "updated_at": member.updated_at.isoformat() if member.updated_at else None,
        },
    )
    return _member_to_response(member)


@router.delete("/{project_id}/members/{user_id}", status_code=204)
async def remove_project_member(
    project_id: int,
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Soft-remove a member from project."""
    await _require_project_member(db, project_id, current_user)
    if current_user.id == user_id:
        raise HTTPException(status_code=400, detail="Use another member account to remove yourself")

    result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
            ProjectMember.deleted_at.is_(None),
        )
    )
    member = result.scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=404, detail="Project member not found")

    member.deleted_at = datetime.utcnow()
    project_result = await db.execute(select(Project).where(Project.id == member.project_id))
    project = project_result.scalar_one_or_none()
    user_result = await db.execute(select(User).where(User.id == member.user_id))
    member_user = user_result.scalar_one_or_none()
    await sync_service.record_event(
        db,
        entity_type="project_members",
        entity_public_id=member.public_id,
        operation="delete",
        payload={
            "public_id": member.public_id,
            "project_id": member.project_id,
            "project_public_id": project.public_id if project else None,
            "user_id": member.user_id,
            "user_public_id": member_user.public_id if member_user else None,
            "deleted_at": member.deleted_at.isoformat() if member.deleted_at else None,
            "updated_at": member.updated_at.isoformat() if member.updated_at else None,
        },
    )
@router.get("/{project_id}/host-tags")
async def get_project_host_tags(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Return distinct service tags from all hosts (for asset tag filter)."""
    await _require_project_member(db, project_id, current_user)
    result = await db.execute(select(Project).where(Project.id == project_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")
    result = await db.execute(select(Host.tags).where(Host.project_id == project_id))
    rows = result.all()

    def _tags_from_value(val):
        if val is None:
            return []
        if isinstance(val, list):
            return val
        if isinstance(val, str):
            try:
                parsed = json.loads(val)
                return parsed if isinstance(parsed, list) else []
            except (json.JSONDecodeError, TypeError):
                return []
        return []

    tags = sorted({t for row in rows for t in _tags_from_value(row[0])})
    return {"tags": tags}


@router.get("/{project_id}/host-filter-options")
async def get_host_filter_options(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Return distinct discovered OS and SMB signing values for Excel-style filters."""
    await _require_project_member(db, project_id, current_user)
    result = await db.execute(select(Project).where(Project.id == project_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")

    base_filter = (Host.project_id == project_id) & (Host.deleted_at.is_(None))

    os_result = await db.execute(
        select(Host.os_info)
        .where(base_filter)
        .where(Host.os_info.isnot(None))
        .where(Host.os_info != "")
        .distinct()
    )
    os_values = sorted([row[0] for row in os_result.all()])

    smb_result = await db.execute(
        text(
            "SELECT DISTINCT "
            "CASE "
            "  WHEN json_extract(extra_data, '$.smb_signing') = 1 "
            "    OR json_extract(extra_data, '$.smb_signing') = 'true' THEN 'true' "
            "  WHEN json_extract(extra_data, '$.smb_signing') = 0 "
            "    OR lower(json_extract(extra_data, '$.smb_signing')) = 'false' THEN 'false' "
            "  ELSE 'unknown' "
            "END AS smb_val "
            "FROM hosts "
            "WHERE project_id = :pid AND deleted_at IS NULL"
        ),
        {"pid": project_id},
    )
    smb_signing_values = sorted([row[0] for row in smb_result.all()])

    domain_result = await db.execute(
        text(
            "SELECT DISTINCT json_extract(extra_data, '$.domain') AS d "
            "FROM hosts WHERE project_id = :pid AND deleted_at IS NULL "
            "AND json_extract(extra_data, '$.domain') IS NOT NULL"
        ),
        {"pid": project_id},
    )
    domain_values = sorted([row[0] for row in domain_result.all() if row[0]])

    return {"os_values": os_values, "smb_signing_values": smb_signing_values, "domain_values": domain_values}


@router.get("/{project_id}/variables")
async def get_project_variables(
    project_id: int,
    ad_domain_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Get project variables, optionally filtered by AD domain."""
    await _require_project_member(db, project_id, current_user)
    from app.models.variable import ProjectVariable
    query = (
        select(ProjectVariable)
        .where(ProjectVariable.project_id == project_id)
        .where(ProjectVariable.deleted_at.is_(None))
    )
    if ad_domain_id is not None:
        query = query.where(ProjectVariable.ad_domain_id == ad_domain_id)
    result = await db.execute(query)
    return [
        {
            "id": v.id,
            "key": v.key,
            "value": v.value,
            "var_type": v.var_type,
            "ad_domain_id": v.ad_domain_id,
            "host_id": v.host_id,
        }
        for v in result.scalars().all()
    ]


@router.post("/{project_id}/variables")
async def create_project_variable(
    project_id: int, 
    data: dict, # Expect {"key": "foo", "value": "bar"}
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Create or update a project variable."""
    await _require_project_member(db, project_id, current_user)
    from app.models.variable import ProjectVariable
    
    key = data.get("key")
    value = data.get("value")
    var_type = data.get("var_type", "string")
    
    if not key:
        raise HTTPException(status_code=400, detail="Key required")

    ad_domain_id = data.get("ad_domain_id")
    lookup = (
        select(ProjectVariable)
        .where(ProjectVariable.project_id == project_id)
        .where(ProjectVariable.key == key)
    )
    if ad_domain_id is not None:
        lookup = lookup.where(ProjectVariable.ad_domain_id == ad_domain_id)
    else:
        lookup = lookup.where(ProjectVariable.ad_domain_id.is_(None))
    result = await db.execute(lookup)
    existing = result.scalar_one_or_none()
    
    if existing:
        existing.value = value
        existing.var_type = var_type
        Variable = existing
        operation = "update"
    else:
        var = ProjectVariable(
            project_id=project_id, key=key, value=value, var_type=var_type,
            ad_domain_id=ad_domain_id,
        )
        db.add(var)
        Variable = var
        operation = "create"
        
    await db.commit()
    await db.refresh(Variable)
    await sync_service.record_event(
        db,
        entity_type="project_variables",
        entity_public_id=Variable.public_id,
        operation=operation,
        payload=await _project_variable_sync_payload(db, Variable),
    )
    return {"id": Variable.id, "key": Variable.key, "value": Variable.value, "var_type": Variable.var_type, "ad_domain_id": Variable.ad_domain_id, "host_id": Variable.host_id}


@router.delete("/{project_id}/variables/{variable_id}", status_code=204)
async def delete_project_variable(
    project_id: int, 
    variable_id: int, 
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Delete a project variable."""
    await _require_project_member(db, project_id, current_user)
    from app.models.variable import ProjectVariable
    result = await db.execute(
        select(ProjectVariable)
        .where(ProjectVariable.project_id == project_id)
        .where(ProjectVariable.id == variable_id)
    )
    var = result.scalar_one_or_none()
    if not var:
        raise HTTPException(status_code=404, detail="Variable not found")
    var.deleted_at = datetime.utcnow()
    await sync_service.record_event(
        db,
        entity_type="project_variables",
        entity_public_id=var.public_id,
        operation="delete",
        payload=await _project_variable_sync_payload(db, var),
    )
    await db.delete(var)


@router.get("/{project_id}/smb-credentials")
async def get_smb_credentials(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Get stored SMB credentials for a project."""
    await _require_project_member(db, project_id, current_user)
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    creds = (project.extra_data or {}).get("smb_credentials", {})
    return {
        "username": creds.get("username", ""),
        "password": creds.get("password", ""),
        "domain": creds.get("domain", ""),
    }


@router.put("/{project_id}/smb-credentials")
async def save_smb_credentials(
    project_id: int,
    data: dict,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Save SMB credentials for a project."""
    await _require_project_member(db, project_id, current_user)
    result = await db.execute(select(Project).where(Project.id == project_id))
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    
    extra = dict(project.extra_data or {})
    extra["smb_credentials"] = {
        "username": data.get("username", ""),
        "password": data.get("password", ""),
        "domain": data.get("domain", ""),
    }
    project.extra_data = extra
    await db.flush()
    await db.refresh(project)
    return {"status": "saved"}



@router.post("/{project_id}/export-services")
async def export_services(
    project_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Export hosts and ports grouped by service to the project directory."""
    await _require_project_member(db, project_id, current_user)
    import os
    import shlex
    from app.core.host_runner import host_runner
    
    # 1. Fetch project with hosts and services
    result = await db.execute(
        select(Project)
        .where(Project.id == project_id)
        .options(selectinload(Project.hosts).selectinload(Host.services))
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # 2. Group by service
    # Structure: { "service_name": { "ips": set(), "ports": set() } }
    services_map = {}

    for host in project.hosts:
        if not host.ip_address:
            continue
            
        for svc in host.services:
            if not svc.name or svc.state != "open":
                continue
                
            s_name = svc.name.lower().strip()
            if s_name not in services_map:
                services_map[s_name] = {"ips": set(), "ports": set()}
            
            services_map[s_name]["ips"].add(host.ip_address)
            services_map[s_name]["ports"].add(str(svc.port))

    # 3. Determine output directory
    from app.core.utils import resolve_project_path
    project_path = resolve_project_path(project)

    services_dir = os.path.join(project_path, "services")
    
    # Ensure directory exists using host_runner
    await host_runner.execute_sync(f"mkdir -p {shlex.quote(services_dir)}")

    # 4. Write files
    created_files = []
    
    for s_name, data in services_map.items():
        # Sanitize filename
        safe_name = "".join(c for c in s_name if c.isalnum() or c in ('-', '_'))
        
        # Write Hosts
        if data["ips"]:
            hosts_file = os.path.join(services_dir, f"{safe_name}_open_hosts.txt")
            # Construct content safely
            content = "\n".join(sorted(data["ips"]))
            # Use printf to write file to avoid shell escaping issues with complex content (though IPs are safe)
            cmd = f"printf '%s\n' {shlex.quote(content)} > {shlex.quote(hosts_file)}"
            await host_runner.execute_sync(cmd)
            created_files.append(hosts_file)

        # Write Ports
        if data["ports"]:
            ports_file = os.path.join(services_dir, f"{safe_name}_open_ports.txt")
            sorted_ports = sorted(data["ports"], key=lambda x: int(x) if x.isdigit() else x)
            content = ",".join(sorted_ports)
            cmd = f"printf '%s' {shlex.quote(content)} > {shlex.quote(ports_file)}"
            await host_runner.execute_sync(cmd)
            created_files.append(ports_file)

    return {
        "status": "success", 
        "message": f"Exported {len(services_map)} services to {services_dir}",
        "directory": services_dir,
        "file_count": len(created_files)
    }


@router.post("/{project_id}/script-scan", response_model=GenerateScriptScanResponse)
async def generate_script_scan(
    project_id: int,
    body: GenerateScriptScanRequest,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Generate Service Scan checklist group and items (one per service type). Does not execute; user runs items from the checklist."""
    await _require_project_member(db, project_id, current_user)
    result = await db.execute(select(Project).where(Project.id == project_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Project not found")
    data = await script_scanner.generate_service_scan_items(
        db=db,
        project_id=project_id,
        service_names=body.service_names,
        timing=body.timing,
        extra_args=body.extra_args,
    )
    await db.commit()
    return GenerateScriptScanResponse(
        group_id=data["group_id"],
        items=data["items"],
        skipped_services=data["skipped_services"],
    )


def _project_to_response(project: Project, counts: Optional[dict] = None) -> ProjectResponse:
    """Convert Project model to response schema. Use precomputed counts when provided."""
    if counts is not None:
        host_count = counts.get("host_count", 0)
        member_count = counts.get("member_count", 0)
        progress = counts.get("checklist_progress", 0)
    else:
        unloaded = sa_inspect(project).unloaded
        host_count = len(project.hosts) if "hosts" not in unloaded else 0
        total_items = 0
        completed_items = 0
        if "checklist_groups" not in unloaded:
            for group in (project.checklist_groups or []):
                if "items" not in sa_inspect(group).unloaded:
                    for item in (group.items or []):
                        total_items += 1
                        if item.has_successful_execution:
                            completed_items += 1
        progress = (completed_items / total_items * 100) if total_items > 0 else 0
        member_count = len(project.members) if "members" not in unloaded else 0

    return ProjectResponse(
        id=project.id,
        public_id=project.public_id,
        name=project.name,
        description=project.description,
        scope=project.scope,
        status=project.status.value,
        created_at=project.created_at,
        updated_at=project.updated_at,
        start_date=project.start_date,
        end_date=project.end_date,
        extra_data=project.extra_data,
        created_by_user_id=project.created_by_user_id,
        host_count=host_count,
        execution_count=0,
        checklist_progress=progress,
        member_count=member_count,
    )


def _member_to_response(member: ProjectMember) -> ProjectMemberResponse:
    user = member.user
    return ProjectMemberResponse(
        id=member.id,
        public_id=member.public_id,
        project_id=member.project_id,
        user_id=member.user_id,
        username=user.username,
        email=user.email,
        created_at=member.created_at,
    )

