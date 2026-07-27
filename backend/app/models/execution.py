"""Execution models for tracking task runs and their outputs."""
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
    Enum as SQLEnum,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
import enum

from app.database import Base

if TYPE_CHECKING:
    from app.models.checklist import ChecklistItem
    from app.models.host import Host
    from app.models.file import DiscoveredFile
    from app.models.user import User


class ExecutionStatus(str, enum.Enum):
    """Execution lifecycle status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class Execution(Base):
    """
    Execution record for a checklist item run.
    
    Each re-run creates a new version, preserving all historical data.
    
    Attributes:
        id: Primary key
        item_id: Foreign key to checklist item
        host_id: Foreign key to target host (optional for global tasks)
        version: Version number (auto-incrementing per item)
        status: Execution status
        command: Actual command executed (after variable substitution)
        stdout: Standard output
        stderr: Standard error
        exit_code: Process exit code
        parsed_output: JSON with regex-parsed data
        started_at: When execution started
        completed_at: When execution finished
    """
    __tablename__ = "executions"
    __table_args__ = (
        Index(
            "ix_executions_item_host_status",
            "item_id",
            "host_id",
            "status",
        ),
        Index(
            "ix_executions_flow_status",
            "flow_execution_id",
            "status",
        ),
        Index(
            "ix_executions_item_version",
            "item_id",
            "version",
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
    item_id: Mapped[int] = mapped_column(
        ForeignKey("checklist_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    host_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("hosts.id", ondelete="SET NULL"),
        nullable=True,
        index=True
    )
    
    # Versioning
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    
    # Status tracking
    status: Mapped[ExecutionStatus] = mapped_column(
        SQLEnum(ExecutionStatus),
        default=ExecutionStatus.PENDING,
        nullable=False
    )
    
    # Command executed
    command: Mapped[str] = mapped_column(Text, nullable=False)
    
    # Variables used
    variables_used: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    
    # Output capture
    stdout: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    stderr: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    exit_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    # Parsed output for chaining
    parsed_output: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # Example: {
    #     "open_ports": ["22", "80", "443"],
    #     "services": [{"port": 22, "name": "ssh", "version": "OpenSSH 8.0"}],
    #     "os_detection": "Linux 5.x"
    # }
    
    # Alerts triggered
    alerts_triggered: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    
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
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True, index=True)

    # Audit
    started_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    
    # Flow context (if part of a flow execution)
    flow_execution_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, index=True)
    flow_step_index: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    # Relationships
    checklist_item: Mapped["ChecklistItem"] = relationship(
        "ChecklistItem",
        back_populates="executions"
    )
    
    host: Mapped[Optional["Host"]] = relationship(
        "Host",
        back_populates="executions"
    )
    started_by_user: Mapped[Optional["User"]] = relationship("User")
    
    outputs: Mapped[List["ExecutionOutput"]] = relationship(
        "ExecutionOutput",
        back_populates="execution",
        cascade="all, delete-orphan",
        lazy="raise"
    )
    
    discovered_files: Mapped[List["DiscoveredFile"]] = relationship(
        "DiscoveredFile",
        back_populates="execution",
        cascade="all, delete-orphan",
        lazy="raise"
    )
    
    def __repr__(self) -> str:
        return f"<Execution(id={self.id}, item_id={self.item_id}, v{self.version}, status={self.status})>"
    
    @property
    def duration(self) -> Optional[float]:
        """Calculate execution duration in seconds."""
        if self.started_at and self.completed_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None
    
    @property
    def is_running(self) -> bool:
        """Check if execution is currently running."""
        return self.status == ExecutionStatus.RUNNING
    
    @property
    def is_success(self) -> bool:
        """Check if execution completed successfully."""
        return self.status == ExecutionStatus.COMPLETED and self.exit_code == 0


class ExecutionOutput(Base):
    """
    Parsed output key-value pairs from an execution.
    
    Used for the {execution_id.key} templating system.
    
    Attributes:
        id: Primary key
        execution_id: Foreign key to parent execution
        key: Output key name
        value: Output value (as string)
        data_type: Type hint for value (string, int, list, json)
    """
    __tablename__ = "execution_outputs"
    __table_args__ = (
        Index(
            "ix_execution_outputs_exec_key",
            "execution_id",
            "key",
        ),
    )
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    execution_id: Mapped[int] = mapped_column(
        ForeignKey("executions.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    
    key: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    data_type: Mapped[str] = mapped_column(String(50), default="string", nullable=False)
    
    # Relationships
    execution: Mapped["Execution"] = relationship(
        "Execution",
        back_populates="outputs"
    )
    
    def __repr__(self) -> str:
        return f"<ExecutionOutput(id={self.id}, key='{self.key}')>"
    
    def get_typed_value(self):
        """Return value converted to appropriate type."""
        import json
        
        if self.data_type == "int":
            return int(self.value)
        elif self.data_type == "float":
            return float(self.value)
        elif self.data_type == "bool":
            return self.value.lower() in ("true", "1", "yes")
        elif self.data_type in ("list", "json", "dict"):
            try:
                return json.loads(self.value)
            except json.JSONDecodeError:
                # Fallback for Python-style string representation (e.g. ['a', 'b'])
                try:
                    import ast
                    return ast.literal_eval(self.value)
                except (ValueError, SyntaxError):
                    return self.value
        else:
            return self.value
