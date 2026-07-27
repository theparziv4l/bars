"""Pydantic schemas for Project."""
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict


class ProjectScope(BaseModel):
    """Scope definition for a project."""
    ips: List[str] = Field(default_factory=list, description="Individual IP addresses")
    hostnames: List[str] = Field(default_factory=list, description="Short hostnames")
    fqdns: List[str] = Field(default_factory=list, description="Fully qualified domain names")
    cidrs: List[str] = Field(default_factory=list, description="CIDR ranges (e.g., 192.168.1.0/24)")
    exclusions: List[str] = Field(default_factory=list, description="Targets to exclude")


class ProjectBase(BaseModel):
    """Base project schema."""
    name: str = Field(..., min_length=1, max_length=255, description="Project name")
    description: Optional[str] = Field(None, description="Project description")
    scope: ProjectScope = Field(default_factory=ProjectScope, description="Scope definition")
    start_date: Optional[datetime] = Field(None, description="Testing start date")
    end_date: Optional[datetime] = Field(None, description="Testing end date")
    extra_data: dict = Field(default_factory=dict, description="Additional metadata")


class ProjectCreate(ProjectBase):
    """Schema for creating a project."""
    domains: List[str] = Field(default_factory=list, description="AD domain names to create (e.g. ['test.local'])")


class ProjectUpdate(BaseModel):
    """Schema for updating a project."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    scope: Optional[ProjectScope] = None
    status: Optional[str] = Field(None, pattern="^(planning|active|paused|completed|archived)$")
    start_date: Optional[datetime] = None
    end_date: Optional[datetime] = None
    extra_data: Optional[dict] = None


class ProjectResponse(ProjectBase):
    """Schema for project response."""
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    status: str
    created_at: datetime
    updated_at: datetime
    public_id: str
    created_by_user_id: Optional[int] = None
    
    # Computed stats
    host_count: int = 0
    execution_count: int = 0
    checklist_progress: float = 0.0
    member_count: int = 0


class ProjectListResponse(BaseModel):
    """Schema for paginated project list."""
    items: List[ProjectResponse]
    total: int
    page: int
    per_page: int
    pages: int
