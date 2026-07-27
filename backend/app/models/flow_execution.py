"""FlowExecution model — tracks the lifecycle of a single flow run."""
from datetime import datetime
import uuid
from typing import Optional, List, TYPE_CHECKING
from sqlalchemy import String, DateTime, JSON, Text, Integer, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

if TYPE_CHECKING:
    from app.models.flow import Flow
    from app.models.execution import Execution
    from app.models.user import User


class FlowExecution(Base):
    __tablename__ = "flow_executions"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    flow_id: Mapped[int] = mapped_column(
        ForeignKey("flows.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(20), default="pending", nullable=False
    )

    variables: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)
    target: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    targets: Mapped[list] = mapped_column(JSON, default=list, nullable=False)
    host_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("hosts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )

    started_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    paused_at_step: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Relationships
    flow: Mapped["Flow"] = relationship("Flow", back_populates="executions")
    started_by_user: Mapped[Optional["User"]] = relationship("User")
    task_executions: Mapped[List["Execution"]] = relationship(
        "Execution",
        primaryjoin="foreign(Execution.flow_execution_id) == FlowExecution.id",
        viewonly=True,
        lazy="raise",
    )

    def __repr__(self) -> str:
        return f"<FlowExecution(id={self.id}, flow_id={self.flow_id}, status={self.status})>"
