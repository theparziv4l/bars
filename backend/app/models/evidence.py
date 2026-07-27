"""Evidence model — proof attached to findings/hosts/executions for the report.

Distinct from DiscoveredFile (SMB loot): this is operator-supplied proof — screenshots,
uploaded files, or captured output excerpts.
"""
from datetime import datetime
import uuid
from typing import Optional

from sqlalchemy import String, Integer, ForeignKey, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base


class Evidence(Base):
    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(
        String(36), default=lambda: str(uuid.uuid4()), nullable=False, unique=True, index=True
    )
    project_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    finding_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("findings.id", ondelete="CASCADE"), nullable=True, index=True
    )
    host_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("hosts.id", ondelete="SET NULL"), nullable=True
    )
    execution_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("executions.id", ondelete="SET NULL"), nullable=True
    )

    kind: Mapped[str] = mapped_column(String(24), default="file", nullable=False)  # screenshot|file|text|output_excerpt
    caption: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    local_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)   # for uploaded files/screenshots
    content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)      # for text/output excerpts
    mime: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    filename: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)

    def __repr__(self) -> str:
        return f"<Evidence(id={self.id}, kind={self.kind}, finding_id={self.finding_id})>"
