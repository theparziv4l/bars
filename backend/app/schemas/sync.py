"""Schemas for sync push/pull APIs."""
from datetime import datetime
from typing import Any, Dict, List

from pydantic import BaseModel, ConfigDict, Field


class SyncEventMessage(BaseModel):
    """Serializable event envelope."""

    event_id: str
    entity_type: str
    entity_public_id: str
    operation: str
    payload: Dict[str, Any] = Field(default_factory=dict)
    logical_ts: int
    source_node_id: str
    created_at: datetime


class SyncPushRequest(BaseModel):
    """Incoming remote push payload."""

    source_node_id: str
    events: List[SyncEventMessage]


class SyncPushResponse(BaseModel):
    """Push status summary."""

    applied: int
    skipped: int
    latest_logical_ts: int


class SyncPullResponse(BaseModel):
    """Pull response payload."""

    node_id: str
    latest_logical_ts: int
    events: List[SyncEventMessage]


class SyncPeerCreate(BaseModel):
    """Request body for adding a sync peer."""

    base_url: str
    label: str | None = None


class SyncPeerResponse(BaseModel):
    """Sync peer record for API responses."""
    model_config = ConfigDict(from_attributes=True)

    id: int
    base_url: str
    label: str | None
    created_at: datetime
