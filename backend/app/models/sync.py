"""Models for multi-node synchronization event log and cursors."""
from __future__ import annotations

from datetime import datetime
import uuid

from sqlalchemy import DateTime, Index, Integer, JSON, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class SyncEvent(Base):
    """Immutable synchronization event."""

    __tablename__ = "sync_events"
    __table_args__ = (
        UniqueConstraint("event_id", name="uq_sync_events_event_id"),
        Index("ix_sync_events_entity_scope", "entity_type", "entity_public_id"),
        # NB: logical_ts already has index=True on the column (auto-named
        # ix_sync_events_logical_ts); a second explicit Index of the same name made
        # create_all emit it twice and fail on a fresh DB.
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(
        String(36),
        default=lambda: str(uuid.uuid4()),
        nullable=False,
        unique=True,
        index=True,
    )
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False, index=True)
    entity_public_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    operation: Mapped[str] = mapped_column(String(24), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    logical_ts: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    source_node_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class SyncCursor(Base):
    """Stores latest applied/pushed logical timestamp per peer/direction."""

    __tablename__ = "sync_cursors"
    __table_args__ = (
        UniqueConstraint("peer_node_id", "direction", name="uq_sync_cursor_peer_direction"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    peer_node_id: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False)  # "pull" | "push"
    last_logical_ts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )


class SyncPeer(Base):
    """Dynamically configured sync peer (collaborator node) from in-app UI."""

    __tablename__ = "sync_peers"
    __table_args__ = (UniqueConstraint("base_url", name="uq_sync_peers_base_url"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    base_url: Mapped[str] = mapped_column(String(512), nullable=False, unique=True, index=True)
    label: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
