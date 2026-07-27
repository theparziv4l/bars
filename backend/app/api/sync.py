"""Sync transport endpoints for multi-node replication."""
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.core.auth import get_current_user_optional
from app.core.sync import sync_service
from app.database import get_db
from app.models.project import Project
from app.models.sync import SyncPeer
from app.schemas.sync import (
    SyncEventMessage,
    SyncPeerCreate,
    SyncPeerResponse,
    SyncPullResponse,
    SyncPushRequest,
    SyncPushResponse,
)
from app.models.user import User

router = APIRouter()


def _normalize_base_url(url: str) -> str:
    u = (url or "").strip().rstrip("/")
    if not u:
        raise ValueError("base_url is required")
    if not u.startswith(("http://", "https://")):
        raise ValueError("base_url must start with http:// or https://")
    return u


@router.get("/pull", response_model=SyncPullResponse)
async def pull_events(
    since_logical_ts: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=5000),
    project_public_id: Optional[str] = Query(None, description="If set, return only events for this project"),
    db: AsyncSession = Depends(get_db),
):
    """Return local events newer than provided cursor, optionally filtered by project."""
    if not settings.SYNC_ENABLED:
        raise HTTPException(status_code=503, detail="Sync is disabled")
    events = await sync_service.pull_events(
        db,
        since_logical_ts=since_logical_ts,
        limit=limit,
        project_public_id=project_public_id,
    )
    payload: List[SyncEventMessage] = [
        SyncEventMessage(
            event_id=e.event_id,
            entity_type=e.entity_type,
            entity_public_id=e.entity_public_id,
            operation=e.operation,
            payload=e.payload,
            logical_ts=e.logical_ts,
            source_node_id=e.source_node_id,
            created_at=e.created_at,
        )
        for e in events
    ]
    latest = payload[-1].logical_ts if payload else since_logical_ts
    return SyncPullResponse(node_id=settings.NODE_ID, latest_logical_ts=latest, events=payload)


@router.post("/push", response_model=SyncPushResponse)
async def push_events(
    data: SyncPushRequest,
    db: AsyncSession = Depends(get_db),
):
    """Ingest remote events from peer node."""
    if not settings.SYNC_ENABLED:
        raise HTTPException(status_code=503, detail="Sync is disabled")
    result = await sync_service.ingest_remote_events(
        db,
        peer_node_id=data.source_node_id,
        events=[event.model_dump() for event in data.events],
    )
    return SyncPushResponse(
        applied=result.applied,
        skipped=result.skipped,
        latest_logical_ts=result.latest_logical_ts,
    )


@router.get("/peers", response_model=List[SyncPeerResponse])
async def list_peers(
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
):
    """List dynamically configured sync peers (in-app)."""
    if not settings.SYNC_ENABLED:
        raise HTTPException(status_code=503, detail="Sync is disabled")
    result = await db.execute(select(SyncPeer).order_by(SyncPeer.id))
    peers = result.scalars().all()
    return [SyncPeerResponse.model_validate(p) for p in peers]


@router.post("/peers", response_model=SyncPeerResponse)
async def add_peer(
    data: SyncPeerCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
):
    """Add a sync peer by base URL and optional label."""
    if not settings.SYNC_ENABLED:
        raise HTTPException(status_code=503, detail="Sync is disabled")
    try:
        base_url = _normalize_base_url(data.base_url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    existing = await db.execute(select(SyncPeer).where(SyncPeer.base_url == base_url))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Peer with this URL already exists")
    peer = SyncPeer(base_url=base_url, label=data.label or None)
    db.add(peer)
    await db.commit()
    await db.refresh(peer)
    from app.core.sync_worker import sync_worker
    sync_worker.invalidate_peer_cache()
    return SyncPeerResponse.model_validate(peer)


@router.delete("/peers/{peer_id}")
async def delete_peer(
    peer_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User | None = Depends(get_current_user_optional),
):
    """Remove a sync peer by id."""
    if not settings.SYNC_ENABLED:
        raise HTTPException(status_code=503, detail="Sync is disabled")
    result = await db.execute(select(SyncPeer).where(SyncPeer.id == peer_id))
    peer = result.scalar_one_or_none()
    if not peer:
        raise HTTPException(status_code=404, detail="Peer not found")
    await db.delete(peer)
    await db.commit()
    from app.core.sync_worker import sync_worker
    sync_worker.invalidate_peer_cache()
    return {"ok": True}


@router.post("/run")
async def run_sync_now(
    project_id: Optional[int] = Query(None, description="If set, sync only this project with all peers"),
    current_user: User | None = Depends(get_current_user_optional),
    db: AsyncSession = Depends(get_db),
):
    """Trigger one full sync cycle with all peers (pull and push). Optionally scope to one project."""
    if not settings.SYNC_ENABLED:
        raise HTTPException(status_code=503, detail="Sync is disabled")
    from app.core.sync_worker import sync_worker
    project_public_id = None
    if project_id is not None:
        result = await db.execute(select(Project).where(Project.id == project_id))
        project = result.scalar_one_or_none()
        if not project:
            raise HTTPException(status_code=404, detail="Project not found")
        project_public_id = project.public_id
    await sync_worker.run_once(project_public_id=project_public_id)
    return {"ok": True, "message": "Sync completed", "project_public_id": project_public_id}


@router.get("/node-info")
async def node_info(
    current_user: User | None = Depends(get_current_user_optional),
):
    """Return this node's id and optional public URL for collaborators."""
    peer_url = (settings.PUBLIC_BASE_URL or "").strip().rstrip("/") or None
    return {"node_id": settings.NODE_ID, "peer_url": peer_url}
