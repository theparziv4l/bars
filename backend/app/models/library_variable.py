"""Library Variable model for global variable templates."""
from typing import Any
from datetime import datetime
from sqlalchemy import String, JSON, DateTime, Boolean, text as sa_text
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.database import Base


class LibraryVariable(Base):
    """
    Global variable templates that can be imported into projects.
    
    Attributes:
        id: Primary key
        key: Variable name
        value: Variable value (JSON serializable)
        var_type: Type of variable (string, file, etc.)
        description: Optional description
        created_at: Timestamp when created
    """
    __tablename__ = "library_variables"
    
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(255), nullable=False, unique=True)
    value: Mapped[Any] = mapped_column(JSON, nullable=False)
    var_type: Mapped[str] = mapped_column(String(50), default="string", nullable=False)
    description: Mapped[str] = mapped_column(String(500), nullable=True)
    is_default_import: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False, server_default=sa_text("0")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), 
        server_default=func.now(),
        nullable=False
    )
    
    def __repr__(self) -> str:
        return f"<LibraryVariable(key='{self.key}')>"
