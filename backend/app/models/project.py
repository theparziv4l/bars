"""Project model for managing pentest engagements."""
from datetime import datetime
import uuid
from typing import Optional, List, TYPE_CHECKING
from sqlalchemy import String, DateTime, JSON, Text, Enum as SQLEnum, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
import enum

from app.database import Base

if TYPE_CHECKING:
    from app.models.host import Host
    from app.models.checklist import ChecklistGroup
    from app.models.flow import Flow
    from app.models.variable import ProjectVariable
    from app.models.user import User, ProjectMember
    from app.models.ad_domain import ADDomain


class ProjectStatus(str, enum.Enum):
    """Project lifecycle status."""
    PLANNING = "planning"
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class Project(Base):
    """
    Project model representing a penetration testing engagement.
    
    Attributes:
        id: Primary key
        name: Project name
        description: Detailed project description
        scope: JSON containing IPs, hostnames, FQDNs, CIDRs
        status: Current project status
        created_at: When the project was created
        start_date: When testing begins
        end_date: When testing ends
        extra_data: Additional project metadata (client info, etc.)
    """
    __tablename__ = "projects"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(
        String(36),
        default=lambda: str(uuid.uuid4()),
        nullable=False,
        unique=True,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Scope definition - supports multiple formats
    scope: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # Example scope structure:
    # {
    #     "ips": ["192.168.1.1", "10.0.0.5"],
    #     "hostnames": ["server1", "db-prod"],
    #     "fqdns": ["www.example.com"],
    #     "cidrs": ["192.168.1.0/24"],
    #     "exclusions": ["192.168.1.100"]
    # }
    
    status: Mapped[ProjectStatus] = mapped_column(
        SQLEnum(ProjectStatus),
        default=ProjectStatus.PLANNING,
        nullable=False
    )
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )
    start_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    end_date: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)

    created_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    
    # Additional metadata
    extra_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    
    # Relationships
    hosts: Mapped[List["Host"]] = relationship(
        "Host",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="raise"
    )
    
    checklist_groups: Mapped[List["ChecklistGroup"]] = relationship(
        "ChecklistGroup",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="raise",
        order_by="ChecklistGroup.order_index"
    )
    
    flows: Mapped[List["Flow"]] = relationship(
        "Flow",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="raise"
    )

    variables: Mapped[List["ProjectVariable"]] = relationship(
        "ProjectVariable",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="raise"
    )

    ad_domains: Mapped[List["ADDomain"]] = relationship(
        "ADDomain",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    members: Mapped[List["ProjectMember"]] = relationship(
        "ProjectMember",
        back_populates="project",
        cascade="all, delete-orphan",
        lazy="raise",
    )

    created_by: Mapped[Optional["User"]] = relationship(
        "User",
        back_populates="created_projects",
        lazy="raise",
    )
    
    def __repr__(self) -> str:
        return f"<Project(id={self.id}, name='{self.name}', status={self.status})>"
    
    def is_in_scope(self, target: str) -> bool:
        """Check if a target is within project scope."""
        import ipaddress
        
        # Check direct matches
        if target in self.scope.get("ips", []):
            return True
        if target in self.scope.get("hostnames", []):
            return True
        if target in self.scope.get("fqdns", []):
            return True
        
        # Check exclusions first
        if target in self.scope.get("exclusions", []):
            return False
        
        # Check CIDR ranges
        try:
            target_ip = ipaddress.ip_address(target)
            for cidr in self.scope.get("cidrs", []):
                network = ipaddress.ip_network(cidr, strict=False)
                if target_ip in network:
                    return True
        except ValueError:
            pass  # Not a valid IP address
        
        return False
