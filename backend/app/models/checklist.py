"""Checklist models for task organization."""
from datetime import datetime
import uuid
from typing import Optional, List, TYPE_CHECKING
from sqlalchemy import String, DateTime, JSON, Text, Integer, ForeignKey, Boolean, text as sa_text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy import inspect as sa_inspect

from app.database import Base

if TYPE_CHECKING:
    from app.models.project import Project
    from app.models.execution import Execution
    from app.models.flow import FlowStep


class ChecklistGroup(Base):
    """
    Checklist group for organizing related tasks.
    
    Examples: Reconnaissance, Enumeration, Exploitation, Post-Exploitation
    
    Attributes:
        id: Primary key
        project_id: Foreign key to parent project
        name: Group name
        description: Group description
        order_index: Display order within project
        icon: Optional icon identifier for UI
        color: Optional color code for UI
    """
    __tablename__ = "checklist_groups"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(
        String(36),
        default=lambda: str(uuid.uuid4()),
        nullable=False,
        unique=True,
        index=True,
    )
    project_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"),
        nullable=True,
        index=True
    )
    ad_domain_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ad_domains.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    order_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    
    # UI customization
    icon: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    color: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    
    # State
    collapsed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_trashed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    
    # Template flag
    is_template: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    
    # Auto-import flags (only meaningful when is_template=True)
    is_default_import: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default=sa_text("0")
    )
    requires_auth: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default=sa_text("0")
    )
    
    # Scan speed profile: stealth, default, fast
    speed_profile: Mapped[str] = mapped_column(
        String(20), default="default", nullable=False, server_default=sa_text("'default'")
    )
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False,
    )
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)
    
    # Relationships
    project: Mapped[Optional["Project"]] = relationship("Project", back_populates="checklist_groups")
    
    items: Mapped[List["ChecklistItem"]] = relationship(
        "ChecklistItem",
        back_populates="group",
        cascade="all, delete-orphan",
        lazy="raise",
        order_by="ChecklistItem.order_index"
    )
    
    def __repr__(self) -> str:
        return f"<ChecklistGroup(id={self.id}, name='{self.name}')>"
    
    @property
    def completion_stats(self) -> dict:
        """Calculate completion statistics for this group."""
        if 'items' in sa_inspect(self).unloaded:
            return {"total": 0, "completed": 0, "percentage": 0}
        if not self.items:
             return {"total": 0, "completed": 0, "percentage": 0}

        total = len(self.items)
        if total == 0:
            return {"total": 0, "completed": 0, "percentage": 0}
        
        completed = sum(1 for item in self.items if item.has_successful_execution)
        return {
            "total": total,
            "completed": completed,
            "percentage": round((completed / total) * 100, 1)
        }


class ChecklistItem(Base):
    """
    Individual checklist item representing a tool or command to execute.
    
    Attributes:
        id: Primary key
        group_id: Foreign key to parent group
        name: Task name (e.g., "Nmap Full Port Scan")
        description: What this task does
        command_template: Command with variable placeholders
        output_regex: Regex patterns for parsing output
        variables: Default variable values
        order_index: Display order within group
        enabled: Whether this item is active
        timeout: Execution timeout in seconds
    """
    __tablename__ = "checklist_items"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(
        String(36),
        default=lambda: str(uuid.uuid4()),
        nullable=False,
        unique=True,
        index=True,
    )
    group_id: Mapped[int] = mapped_column(
        ForeignKey("checklist_groups.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Command configuration
    command_template: Mapped[str] = mapped_column(Text, nullable=False)
    # Example: "nmap -sV -sC -p- {target} -oN {output_file}"
    
    # Output parsing configuration
    output_regex: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # Example: {
    #     "open_ports": r"(\d+)/open",
    #     "services": r"(\d+)/tcp\s+open\s+(\w+)\s+(.+)",
    #     "os_detection": r"OS details: (.+)"
    # }
    
    # Default variables
    variables: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # Example: {"target": "", "ports": "1-65535"}
    
    # Execution settings
    order_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_trashed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    timeout: Mapped[int] = mapped_column(Integer, default=3600, nullable=False)  # 1 hour
    
    # Notification patterns
    alert_patterns: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # Example: {
    #     "critical": [r"VULNERABLE", r"exploit"],
    #     "warning": [r"potentially vulnerable", r"weak"]
    # }

    # Finding-generation template: turns an alert match into a structured Finding.
    # Example: {"title": "SMB Signing Not Required", "severity": "medium",
    #           "cwe": "CWE-326", "remediation": "Enforce SMB signing via GPO",
    #           "references": ["https://..."]}. Empty => fall back to alert_patterns bucket.
    finding_template: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    # Input definitions for dynamic flows
    input_definitions: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # Example: [{"name": "target", "source": "project_scope"}, {"name": "ports", "source": "task_output"}]

    # Storage policy for results
    storage_policy: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # Example: {"save_to_db": true, "create_folder": true, "folder_path": "nmap_scans"}
    
    # Parameter Type Enforcement Schema
    parameter_schema: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # Example: {"target_list": {"type": "file"}, "ports": {"type": "string", "delimiter": ","}}

    # Target filter: resolve {targets} from hosts matching these tags (e.g. {"tags": ["smb", "http"]})
    target_filter: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    
    # Tags for filtering
    tags: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(
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
    group: Mapped["ChecklistGroup"] = relationship("ChecklistGroup", back_populates="items")
    
    executions: Mapped[List["Execution"]] = relationship(
        "Execution",
        back_populates="checklist_item",
        cascade="all, delete-orphan",
        lazy="raise",
        order_by="Execution.version.desc()"
    )
    
    flow_steps: Mapped[List["FlowStep"]] = relationship(
        "FlowStep",
        back_populates="checklist_item",
        lazy="raise"
    )
    
    def __repr__(self) -> str:
        return f"<ChecklistItem(id={self.id}, name='{self.name}')>"
    
    @property
    def has_successful_execution(self) -> bool:
        """Check if this item has at least one successful execution."""
        if 'executions' in sa_inspect(self).unloaded:
            return False
        from app.models.execution import ExecutionStatus
        return any(e.status == ExecutionStatus.COMPLETED and e.exit_code == 0 for e in self.executions)
    
    @property
    def latest_execution(self) -> Optional["Execution"]:
        """Get the most recent execution."""
        if 'executions' in sa_inspect(self).unloaded:
            return None
        if self.executions:
            return self.executions[0]  # Already ordered by version desc
        return None
    
    @property
    def next_version(self) -> int:
        """Get the next version number for execution."""
        if 'executions' in sa_inspect(self).unloaded:
            return 1
        if self.executions:
            return self.executions[0].version + 1
        return 1
