"""Host API endpoints."""
from typing import Dict, List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, inspect as sa_inspect, text
from sqlalchemy.orm import selectinload

from app.core.sync import sync_service, service_sync_payload
from app.database import get_db
from app.models.host import Host, Service
from app.models.project import Project
from app.models.execution import Execution
from app.models.file import DiscoveredFile
from app.schemas.host import (
    HostCreate,
    HostUpdate,
    HostResponse,
    ServiceCreate,
    ServiceResponse,
    HostListResponse,
    HostScriptScanRequest,
    HostScriptScanResponse,
    HostBulkScopeRequest,
)
from app.core import script_scanner

router = APIRouter()


async def _get_host_counts(db: AsyncSession, host_ids: List[int]) -> Dict[int, dict]:
    """Fetch execution_count and file_count for given host IDs."""
    if not host_ids:
        return {}

    exec_rows = await db.execute(
        select(Execution.host_id, func.count(Execution.id).label("cnt"))
        .where(Execution.host_id.in_(host_ids))
        .group_by(Execution.host_id)
    )
    exec_map = {r[0]: r[1] for r in exec_rows.all()}

    file_rows = await db.execute(
        select(DiscoveredFile.host_id, func.count(DiscoveredFile.id).label("cnt"))
        .where(DiscoveredFile.host_id.in_(host_ids))
        .group_by(DiscoveredFile.host_id)
    )
    file_map = {r[0]: r[1] for r in file_rows.all()}

    return {
        hid: {
            "execution_count": exec_map.get(hid, 0),
            "file_count": file_map.get(hid, 0),
        }
        for hid in host_ids
    }


async def _host_sync_payload(db: AsyncSession, host: Host) -> dict:
    project_public_id = None
    project_result = await db.execute(select(Project).where(Project.id == host.project_id))
    project = project_result.scalar_one_or_none()
    if project:
        project_public_id = project.public_id
    return {
        "public_id": host.public_id,
        "project_id": host.project_id,
        "project_public_id": project_public_id,
        "ip_address": host.ip_address,
        "hostname": host.hostname,
        "fqdn": host.fqdn,
        "os_info": host.os_info,
        "status": host.status,
        "notes": host.notes,
        "extra_data": host.extra_data,
        "tags": host.tags,
        "excluded": host.excluded,
        "updated_at": host.updated_at.isoformat() if host.updated_at else None,
        "deleted_at": host.deleted_at.isoformat() if host.deleted_at else None,
    }


def _order_by_expr(query, sort_by: str, sort_order: str):
    """Apply ORDER BY based on sort_by and sort_order (asc/desc)."""
    desc = sort_order.lower() == "desc" if sort_order else False

    if sort_by == "host":
        expr = func.coalesce(Host.hostname, Host.ip_address, "")
    elif sort_by == "ip_address":
        expr = Host.ip_address
    elif sort_by == "os_info":
        expr = Host.os_info
    elif sort_by == "discovered_at":
        expr = Host.discovered_at
    elif sort_by == "smb_signing":
        # JSON: null/absent=0, false=1, true=2 for asc (disabled first, enabled last)
        expr = text(
            "CASE WHEN json_extract(hosts.extra_data, '$.smb_signing') = 1 "
            "OR json_extract(hosts.extra_data, '$.smb_signing') = 'true' THEN 2 "
            "WHEN json_extract(hosts.extra_data, '$.smb_signing') = 0 "
            "OR lower(json_extract(hosts.extra_data, '$.smb_signing')) = 'false' THEN 1 ELSE 0 END"
        )
    elif sort_by == "service_count":
        expr = (
            select(func.count(Service.id))
            .where(Service.host_id == Host.id)
            .correlate(Host)
            .scalar_subquery()
        )
    elif sort_by == "execution_count":
        expr = (
            select(func.count(Execution.id))
            .where(Execution.host_id == Host.id)
            .correlate(Host)
            .scalar_subquery()
        )
    elif sort_by == "file_count":
        expr = (
            select(func.count(DiscoveredFile.id))
            .where(DiscoveredFile.host_id == Host.id)
            .correlate(Host)
            .scalar_subquery()
        )
    else:
        expr = Host.discovered_at

    return query.order_by(expr.desc() if desc else expr.asc())


@router.get("", response_model=HostListResponse)
async def list_hosts(
    project_id: int,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=5000),
    status: Optional[str] = None,
    search: Optional[str] = None,
    tags: Optional[List[str]] = Query(None, description="Filter by service tags (host must have at least one)"),
    smb_signing: Optional[str] = Query(None, description="Filter by SMB signing: true, false, unknown"),
    os_info: Optional[str] = Query(None, alias="os_info", description="Filter by exact OS info value"),
    port: Optional[int] = Query(None, description="Filter hosts with a service on this port"),
    domain: Optional[str] = Query(None, description="Filter by AD domain from extra_data"),
    excluded: Optional[bool] = Query(None, description="Filter by scope: true = excluded only, false = in scope only"),
    sort_by: Optional[str] = Query(None, description="Sort column: host, ip_address, os_info, discovered_at, smb_signing, service_count, execution_count, file_count"),
    sort_order: Optional[str] = Query("asc", description="Sort direction: asc or desc"),
    db: AsyncSession = Depends(get_db)
):
    """List hosts for a project."""
    query = select(Host).where(
        Host.project_id == project_id,
        Host.deleted_at.is_(None),
    ).options(
        selectinload(Host.services),
    )
    
    if status:
        query = query.where(Host.status == status)
    if search:
        query = query.where(
            (Host.ip_address.ilike(f"%{search}%")) |
            (Host.hostname.ilike(f"%{search}%")) |
            (Host.fqdn.ilike(f"%{search}%")) |
            (Host.os_info.ilike(f"%{search}%"))
        )
    if smb_signing:
        if smb_signing == "true":
            query = query.where(text(
                "json_extract(hosts.extra_data, '$.smb_signing') = 1 "
                "OR json_extract(hosts.extra_data, '$.smb_signing') = 'true'"
            ))
        elif smb_signing == "false":
            query = query.where(text(
                "json_extract(hosts.extra_data, '$.smb_signing') = 0 "
                "OR lower(json_extract(hosts.extra_data, '$.smb_signing')) = 'false'"
            ))
        elif smb_signing == "unknown":
            query = query.where(text(
                "json_extract(hosts.extra_data, '$.smb_signing') IS NULL"
            ))
    if excluded is not None:
        query = query.where(Host.excluded.is_(excluded))
    if os_info:
        query = query.where(Host.os_info == os_info)
    if port is not None:
        query = query.where(text(
            "EXISTS (SELECT 1 FROM services WHERE services.host_id = hosts.id AND services.port = :port_val)"
        ))
    if domain:
        query = query.where(text(
            "json_extract(hosts.extra_data, '$.domain') = :domain_val"
        ))
    tag_params = {}
    if tags:
        # SQLite: filter hosts whose tags JSON array contains at least one of the requested tags
        placeholders = ", ".join([f":tag_{i}" for i in range(len(tags))])
        tag_filter = text(
            f"EXISTS (SELECT 1 FROM json_each(hosts.tags) WHERE json_each.value IN ({placeholders}))"
        )
        tag_params = {f"tag_{i}": t for i, t in enumerate(tags)}
        query = query.where(tag_filter)
    
    bind_params = {**tag_params}
    if domain:
        bind_params["domain_val"] = domain
    if port is not None:
        bind_params["port_val"] = port

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query, bind_params)).scalar()

    if sort_by:
        query = _order_by_expr(query, sort_by, sort_order or "asc")
    else:
        query = query.order_by(Host.discovered_at.desc())
    
    query = query.offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(query, bind_params)
    hosts = result.scalars().all()

    host_ids = [h.id for h in hosts]
    counts_map = await _get_host_counts(db, host_ids) if host_ids else {}

    return HostListResponse(
        items=[_host_to_response(h, counts=counts_map.get(h.id)) for h in hosts],
        total=total, page=page, per_page=per_page
    )


@router.post("", response_model=HostResponse, status_code=201)
async def create_host(data: HostCreate, db: AsyncSession = Depends(get_db)):
    """Create a new host."""
    from sqlalchemy.orm.attributes import set_committed_value

    host = Host(**data.model_dump())
    db.add(host)
    await db.flush()
    await db.refresh(host)
    set_committed_value(host, 'services', [])
    set_committed_value(host, 'executions', [])
    set_committed_value(host, 'discovered_files', [])
    await sync_service.record_event(
        db,
        entity_type="hosts",
        entity_public_id=host.public_id,
        operation="create",
        payload=await _host_sync_payload(db, host),
    )
    return _host_to_response(host)


@router.post("/bulk-scope")
async def bulk_set_scope(data: HostBulkScopeRequest, db: AsyncSession = Depends(get_db)):
    """Mark hosts in or out of scope in one shot.

    Declared before ``/{host_id}`` so the literal path wins over the int path param.
    """
    result = await db.execute(
        select(Host).where(Host.id.in_(data.host_ids), Host.deleted_at.is_(None))
    )
    hosts = result.scalars().all()
    if not hosts:
        raise HTTPException(status_code=404, detail="No matching hosts")

    for host in hosts:
        host.excluded = data.excluded
    await db.flush()

    for host in hosts:
        await sync_service.record_event(
            db,
            entity_type="hosts",
            entity_public_id=host.public_id,
            operation="update",
            payload=await _host_sync_payload(db, host),
        )

    return {"updated": len(hosts), "excluded": data.excluded}


@router.get("/{host_id}", response_model=HostResponse)
async def get_host(host_id: int, db: AsyncSession = Depends(get_db)):
    """Get host by ID with services."""
    result = await db.execute(
        select(Host).where(Host.id == host_id).options(selectinload(Host.services))
    )
    host = result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")
    counts_map = await _get_host_counts(db, [host_id])
    return _host_to_response(host, counts=counts_map.get(host_id))


@router.put("/{host_id}", response_model=HostResponse)
async def update_host(host_id: int, data: HostUpdate, db: AsyncSession = Depends(get_db)):
    """Update a host."""
    result = await db.execute(
        select(Host).where(Host.id == host_id)
        .options(selectinload(Host.services))
    )
    host = result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")
    
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(host, field, value)
    
    await db.flush()
    await sync_service.record_event(
        db,
        entity_type="hosts",
        entity_public_id=host.public_id,
        operation="update",
        payload=await _host_sync_payload(db, host),
    )
    return _host_to_response(host)


@router.delete("/{host_id}", status_code=204)
async def delete_host(host_id: int, db: AsyncSession = Depends(get_db)):
    """Delete a host."""
    result = await db.execute(select(Host).where(Host.id == host_id))
    host = result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")
    host.deleted_at = host.deleted_at or datetime.utcnow()
    await sync_service.record_event(
        db,
        entity_type="hosts",
        entity_public_id=host.public_id,
        operation="delete",
        payload=await _host_sync_payload(db, host),
    )
    await db.delete(host)


@router.post("/{host_id}/services", response_model=ServiceResponse, status_code=201)
async def add_service(host_id: int, data: ServiceCreate, db: AsyncSession = Depends(get_db)):
    """Add a service to a host."""
    result = await db.execute(select(Host).where(Host.id == host_id))
    if not result.scalar_one_or_none():
        raise HTTPException(status_code=404, detail="Host not found")
    
    service = Service(host_id=host_id, **data.model_dump())
    db.add(service)
    await db.flush()
    await db.refresh(service)
    payload = await service_sync_payload(db, service)
    await sync_service.record_event(
        db,
        entity_type="services",
        entity_public_id=service.public_id,
        operation="create",
        payload=payload,
    )
    return _service_to_response(service)


@router.post("/{host_id}/script-scan", response_model=HostScriptScanResponse)
async def start_host_script_scan(
    host_id: int,
    body: HostScriptScanRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Run NSE script scan for this host (all discovered services). Execution runs in background; results in host execution history."""
    try:
        data = await script_scanner.launch_host_script_scan(
            db=db,
            host_id=host_id,
            service_names=body.service_names,
            timing=body.timing,
            extra_args=body.extra_args,
            background_tasks=background_tasks,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return HostScriptScanResponse(
        execution_id=data["execution_id"],
        command=data["command"],
        host_id=data["host_id"],
    )


@router.get("/{host_id}/timeline")
async def get_host_timeline(host_id: int, limit: int = 50, db: AsyncSession = Depends(get_db)):
    """Get execution timeline for a host."""
    result = await db.execute(
        select(Host).where(Host.id == host_id).options(selectinload(Host.executions))
    )
    host = result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")
    
    executions = sorted(host.executions, key=lambda e: e.created_at, reverse=True)[:limit]
    return [{
        "id": e.id, "item_id": e.item_id, "status": e.status.value,
        "version": e.version, "started_at": e.started_at, "completed_at": e.completed_at
    } for e in executions]


def _host_to_response(host: Host, counts: Optional[dict] = None) -> HostResponse:
    unloaded = sa_inspect(host).unloaded
    services = host.services if "services" not in unloaded else []
    if counts is not None:
        execution_count = counts.get("execution_count", 0)
        file_count = counts.get("file_count", 0)
    else:
        execution_count = (
            len(host.executions)
            if "executions" not in unloaded and host.executions
            else 0
        )
        file_count = (
            len(host.discovered_files)
            if "discovered_files" not in unloaded and host.discovered_files
            else 0
        )
    return HostResponse(
        id=host.id,
        project_id=host.project_id,
        ip_address=host.ip_address,
        hostname=host.hostname,
        fqdn=host.fqdn,
        os_info=host.os_info,
        status=host.status,
        notes=host.notes,
        extra_data=host.extra_data,
        tags=host.tags if hasattr(host, "tags") and host.tags is not None else [],
        excluded=bool(getattr(host, "excluded", False)),
        discovered_at=host.discovered_at,
        updated_at=host.updated_at,
        display_name=host.display_name,
        services=[_service_to_response(s) for s in (services or [])],
        service_count=len(services) if services else 0,
        execution_count=execution_count,
        file_count=file_count,
    )


def _service_to_response(service: Service) -> ServiceResponse:
    return ServiceResponse(
        id=service.id, host_id=service.host_id, port=service.port,
        protocol=service.protocol, name=service.name, version=service.version,
        product=service.product, state=service.state, banner=service.banner,
        ssl=service.ssl, extra_data=service.extra_data, discovered_at=service.discovered_at,
        display_name=service.display_name
    )
