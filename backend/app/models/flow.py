"""Flow models for automation pipelines."""
from datetime import datetime
import uuid
from typing import Optional, List, TYPE_CHECKING
from sqlalchemy import String, DateTime, JSON, Text, Integer, ForeignKey, Boolean, text as sa_text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.project import Project
    from app.models.checklist import ChecklistItem
    from app.models.flow_execution import FlowExecution


class Flow(Base):
    """
    Flow/Playbook for chaining multiple checklist items.
    
    Flows define automated sequences where output from one step
    becomes input to the next via the {step_id.output_key} syntax.
    
    Attributes:
        id: Primary key
        project_id: Foreign key to parent project
        name: Flow name
        description: What this flow does
        is_template: If true, this is a reusable template
        flow_definition: Full flow configuration (for import/export)
    """
    __tablename__ = "flows"
    
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
    
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    # Template flag for reusable flows
    is_template: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    
    # Auto-import flags (only meaningful when is_template=True)
    is_default_import: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default=sa_text("0")
    )
    requires_auth: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default=sa_text("0")
    )

    ad_domain_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ad_domains.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    
    # Full flow definition for export/import
    flow_definition: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # Example: {
    #     "name": "Web Recon",
    #     "steps": [
    #         {
    #             "item_id": 1,
    #             "input_mapping": {"target": "{target}"}
    #         },
    #         {
    #             "item_id": 2,
    #             "input_mapping": {"ports": "{1.open_ports}"}
    #         }
    #     ],
    #     "variables": {"target": ""}
    # }
    
    # Tags for categorization
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
    
    # Relationships
    project: Mapped[Optional["Project"]] = relationship("Project", back_populates="flows")
    
    steps: Mapped[List["FlowStep"]] = relationship(
        "FlowStep",
        back_populates="flow",
        cascade="all, delete-orphan",
        lazy="raise",
        order_by="FlowStep.order_index"
    )

    executions: Mapped[List["FlowExecution"]] = relationship(
        "FlowExecution",
        back_populates="flow",
        cascade="all, delete-orphan",
        lazy="raise",
        order_by="FlowExecution.created_at.desc()",
    )
    
    def __repr__(self) -> str:
        return f"<Flow(id={self.id}, name='{self.name}')>"


class FlowStep(Base):
    """
    Individual step in a flow pipeline.
    
    Attributes:
        id: Primary key
        flow_id: Foreign key to parent flow
        checklist_item_id: Foreign key to the task to execute
        order_index: Execution order in the flow
        input_mapping: How to map outputs from previous steps
        condition: Optional condition for executing this step
        on_failure: What to do if this step fails (stop, continue, skip_remaining)
    """
    __tablename__ = "flow_steps"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    public_id: Mapped[str] = mapped_column(
        String(36),
        default=lambda: str(uuid.uuid4()),
        nullable=False,
        unique=True,
        index=True,
    )
    flow_id: Mapped[int] = mapped_column(
        ForeignKey("flows.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    checklist_item_id: Mapped[int] = mapped_column(
        ForeignKey("checklist_items.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )
    
    order_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    
    # Variable mapping from previous steps
    input_mapping: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # Example: {
    #     "target": "{1.discovered_hosts[0]}",
    #     "ports": "{2.open_ports}"
    # }
    
    # Conditional execution
    condition: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Example: "{1.exit_code} == 0 and len({1.open_ports}) > 0"
    
    # Failure handling
    on_failure: Mapped[str] = mapped_column(String(50), default="stop", nullable=False)
    # Options: "stop", "continue", "skip_remaining"
    
    # Step-specific timeout override
    timeout_override: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Target handling: inherit | all | filtered | single
    target_mode: Mapped[str] = mapped_column(String(20), default="inherit", nullable=False)
    # Tag-based host filter (used when target_mode == "filtered")
    target_filter: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)

    # UI Position for the graph editor
    ui_position: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    # Example: {"x": 100, "y": 200}
    
    # Relationships
    flow: Mapped["Flow"] = relationship("Flow", back_populates="steps")
    
    checklist_item: Mapped["ChecklistItem"] = relationship(
        "ChecklistItem",
        back_populates="flow_steps"
    )
    
    def __repr__(self) -> str:
        return f"<FlowStep(id={self.id}, flow_id={self.flow_id}, order={self.order_index})>"
