"""Host and Service models for asset management."""
from datetime import datetime
import uuid
from typing import Optional, List, TYPE_CHECKING
from sqlalchemy import (
    String,
    DateTime,
    JSON,
    Text,
    Integer,
    ForeignKey,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.project import Project
    from app.models.execution import Execution
    from app.models.file import DiscoveredFile


class Host(Base):
    """
    Host model representing a discovered or defined target system.
    
    Attributes:
        id: Primary key
        project_id: Foreign key to parent project
        ip_address: IPv4 or IPv6 address
        hostname: Short hostname
        fqdn: Fully qualified domain name
        os_info: Detected operating system information
        status: Host status (up, down, unknown)
        notes: Free-form notes about the host
        extra_data: Additional structured data
    """
    __tablename__ = "hosts"
    __table_args__ = (
        Index(
            "ix_hosts_project_ip",
            "project_id",
            "ip_address",
        ),
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
        index=True
    )
    
    # Identification
    ip_address: Mapped[Optional[str]] = mapped_column(String(45), nullable=True, index=True)
    hostname: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    fqdn: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, index=True)
    
    # Discovery info
    os_info: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status: Mapped[str] = mapped_column(String(50), default="unknown", nullable=False)
    
    # Additional data
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extra_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # extra_data can include: mac_address, ttl, hops, discovery_method, etc.
    
    # Service-type tags (e.g. ["http", "smb", "ssh"]) for target filtering
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    
    # Timestamps
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    
    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="hosts")
    
    services: Mapped[List["Service"]] = relationship(
        "Service",
        back_populates="host",
        cascade="all, delete-orphan",
        lazy="raise",
        order_by="Service.port"
    )
    
    executions: Mapped[List["Execution"]] = relationship(
        "Execution",
        back_populates="host",
        lazy="raise"
    )
    
    discovered_files: Mapped[List["DiscoveredFile"]] = relationship(
        "DiscoveredFile",
        back_populates="host",
        cascade="all, delete-orphan",
        lazy="raise"
    )
    
    def __repr__(self) -> str:
        identifier = self.ip_address or self.hostname or self.fqdn or "unknown"
        return f"<Host(id={self.id}, target='{identifier}')>"
    
    @property
    def display_name(self) -> str:
        """Return the best available identifier for display."""
        if self.hostname:
            return self.hostname
        if self.fqdn:
            return self.fqdn
        if self.ip_address:
            return self.ip_address
        return f"Host #{self.id}"


class Service(Base):
    """
    Service model representing a network service on a host.
    
    Attributes:
        id: Primary key
        host_id: Foreign key to parent host
        port: Port number
        protocol: Protocol (tcp, udp)
        name: Service name (http, ssh, smb, etc.)
        version: Detected version string
        state: Port state (open, closed, filtered)
        banner: Service banner if captured
        extra_data: Additional service details
    """
    __tablename__ = "services"
    __table_args__ = (
        Index(
            "ix_services_host_port_proto",
            "host_id",
            "port",
            "protocol",
        ),
    )
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(
        String(36),
        default=lambda: str(uuid.uuid4()),
        nullable=False,
        unique=True,
        index=True,
    )
    host_id: Mapped[int] = mapped_column(
        ForeignKey("hosts.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    
    # Service identification
    port: Mapped[int] = mapped_column(Integer, nullable=False)
    protocol: Mapped[str] = mapped_column(String(10), default="tcp", nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    version: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    product: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    
    # State
    state: Mapped[str] = mapped_column(String(50), default="open", nullable=False)
    
    # Additional info
    banner: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ssl: Mapped[bool] = mapped_column(default=False, nullable=False)
    extra_data: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    
    # Timestamps
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )
    
    # Relationships
    host: Mapped["Host"] = relationship("Host", back_populates="services")
    
    def __repr__(self) -> str:
        return f"<Service(id={self.id}, port={self.port}/{self.protocol}, name='{self.name}')>"
    
    @property
    def display_name(self) -> str:
        """Return formatted service identifier."""
        name = self.name or "unknown"
        version = f" {self.version}" if self.version else ""
        return f"{self.port}/{self.protocol} ({name}{version})"
