"""Checklist claim model for collaborative execution ownership."""
from __future__ import annotations

from datetime import datetime
import uuid
from typing import TYPE_CHECKING, Optional

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.project import Project
    from app.models.checklist import ChecklistItem
    from app.models.host import Host
    from app.models.user import User


class ChecklistClaim(Base):
    """Tracks current ownership claim for a checklist item scope."""

    __tablename__ = "checklist_claims"
    __table_args__ = (
        UniqueConstraint(
            "project_id",
            "item_id",
            "host_scope_key",
            name="uq_checklist_claim_scope",
        ),
        Index("ix_checklist_claims_scope_active", "project_id", "item_id", "is_active"),
        Index("ix_checklist_claims_user", "claimed_by_user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(
        String(36),
        default=lambda: str(uuid.uuid4()),
        nullable=False,
        unique=True,
        index=True,
    )

    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    item_id: Mapped[int] = mapped_column(
        ForeignKey("checklist_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    host_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("hosts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    host_scope_key: Mapped[str] = mapped_column(String(64), nullable=False, index=True)

    claimed_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    claimed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    lease_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    version: Mapped[int] = mapped_column(default=1, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)

    project: Mapped["Project"] = relationship("Project")
    item: Mapped["ChecklistItem"] = relationship("ChecklistItem")
    host: Mapped[Optional["Host"]] = relationship("Host")
    claimed_by_user: Mapped[Optional["User"]] = relationship(
        "User",
        back_populates="checklist_claims",
    )

    def __repr__(self) -> str:
        return (
            f"<ChecklistClaim(project_id={self.project_id}, item_id={self.item_id}, "
            f"host_scope='{self.host_scope_key}', active={self.is_active})>"
        )
