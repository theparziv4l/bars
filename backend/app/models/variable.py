"""Variable models for project context."""
from datetime import datetime
import uuid
from typing import Optional, TYPE_CHECKING, Any, List
from sqlalchemy import String, Integer, ForeignKey, JSON, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.project import Project

class ProjectVariable(Base):
    """
    User-defined variables scoped to a project.
    
    Attributes:
        id: Primary key
        project_id: Foreign key to parent project
        key: Variable name
        value: Variable value (JSON serializable)
    """
    __tablename__ = "project_variables"
    
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
        index=True
    )
    
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[Any] = mapped_column(JSON, nullable=False) # Use Any for typing but database is JSON
    var_type: Mapped[str] = mapped_column(String(50), default="string", nullable=False)
    
    # Scope (Global if null, specific to Host if set)
    host_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"),
        nullable=True,
        index=True
    )
    ad_domain_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ad_domains.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    
    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="variables")
    
    def __repr__(self) -> str:
        return f"<ProjectVariable(key='{self.key}')>"
