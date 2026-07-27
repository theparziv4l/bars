"""Finding / vulnerability model — persistent pentest findings.

Persists what the ephemeral ``Alert`` (core/notifications.py) only held in memory:
an alert match becomes a structured, deduped Finding linked to its host/execution.
"""
from datetime import datetime
import enum
import uuid
from typing import Optional, TYPE_CHECKING

from sqlalchemy import String, Integer, Float, ForeignKey, JSON, DateTime, Text, Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base

if TYPE_CHECKING:
    pass


class FindingSeverity(str, enum.Enum):
    INFO = "info"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FindingStatus(str, enum.Enum):
    OPEN = "open"
    CONFIRMED = "confirmed"
    REMEDIATED = "remediated"
    ACCEPTED = "accepted"
    FALSE_POSITIVE = "false_positive"


# Higher = worse. Used for rollup, sorting, and severity escalation on dedupe.
SEVERITY_ORDER = {
    FindingSeverity.INFO: 0,
    FindingSeverity.LOW: 1,
    FindingSeverity.MEDIUM: 2,
    FindingSeverity.HIGH: 3,
    FindingSeverity.CRITICAL: 4,
}


class Finding(Base):
    """A persistent security finding — auto-created from alert matches or added manually."""
    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(
        String(36), default=lambda: str(uuid.uuid4()), nullable=False, unique=True, index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )

    title: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    severity: Mapped[FindingSeverity] = mapped_column(
        SQLEnum(FindingSeverity), default=FindingSeverity.INFO, nullable=False, index=True
    )
    status: Mapped[FindingStatus] = mapped_column(
        SQLEnum(FindingStatus), default=FindingStatus.OPEN, nullable=False, index=True
    )

    cvss_vector: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    cvss_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    cwe: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    cve: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    remediation: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    references: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    # Linkage — all optional so a finding can be project-wide or asset/execution scoped.
    host_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("hosts.id", ondelete="SET NULL"), nullable=True, index=True
    )
    service_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("services.id", ondelete="SET NULL"), nullable=True
    )
    item_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("checklist_items.id", ondelete="SET NULL"), nullable=True
    )
    execution_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("executions.id", ondelete="SET NULL"), nullable=True
    )

    source: Mapped[str] = mapped_column(String(20), default="manual", nullable=False)  # alert | manual
    evidence_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # matched snippet
    occurrences: Mapped[int] = mapped_column(Integer, default=1, nullable=False)

    discovered_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)

    def __repr__(self) -> str:
        return f"<Finding(id={self.id}, severity={self.severity}, title={self.title!r})>"
