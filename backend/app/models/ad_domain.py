"""AD Domain model for multi-domain pentest projects."""
from datetime import datetime
import uuid
from typing import Optional, List, TYPE_CHECKING, Any
from sqlalchemy import String, Integer, ForeignKey, JSON, DateTime, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.project import Project


class ADDomain(Base):
    """
    Active Directory domain associated with a project.
    Supports multi-domain environments: parent, child, and trust relationships.
    """
    __tablename__ = "ad_domains"

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

    name: Mapped[str] = mapped_column(String(255), nullable=False)
    netbios_name: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    dc_ip: Mapped[Optional[str]] = mapped_column(String(45), nullable=True)
    dc_fqdn: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    trust_type: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True
    )  # parent, child, forest_trust, external, shortcut

    parent_domain_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ad_domains.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    extra_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)

    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="ad_domains")
    parent_domain: Mapped[Optional["ADDomain"]] = relationship(
        "ADDomain",
        remote_side="ADDomain.id",
        back_populates="child_domains",
    )
    child_domains: Mapped[List["ADDomain"]] = relationship(
        "ADDomain",
        back_populates="parent_domain",
        lazy="raise",
    )

    def __repr__(self) -> str:
        return f"<ADDomain(id={self.id}, name='{self.name}')>"
