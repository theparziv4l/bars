"""Pydantic schemas for Checklist entities."""
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict


class ChecklistItemBase(BaseModel):
    """Base checklist item schema."""
    name: str = Field(..., min_length=1, max_length=255, description="Task name")
    description: Optional[str] = Field(None, description="Task description")
    command_template: str = Field(..., description="Command with variable placeholders")
    output_regex: Dict[str, str] = Field(default_factory=dict, description="Regex patterns for parsing output")
    variables: Dict[str, Any] = Field(default_factory=dict, description="Default variable values")
    input_definitions: Dict[str, Any] = Field(default_factory=dict, description="Input variable definitions")
    storage_policy: Dict[str, Any] = Field(default_factory=dict, description="Result storage policy")
    parameter_schema: Dict[str, Any] = Field(default_factory=dict, description="Parameter type enforcement schema")
    target_filter: Dict[str, Any] = Field(default_factory=dict, description="Target filter e.g. {\"tags\": [\"smb\"]} for resolving {targets}")
    timeout: int = Field(3600, ge=1, description="Execution timeout in seconds")
    alert_patterns: Dict[str, List[str]] = Field(default_factory=dict, description="Alert trigger patterns")
    finding_template: Dict[str, Any] = Field(default_factory=dict, description="Finding metadata for alert matches: {title, severity, cwe, remediation, references}")
    tags: List[str] = Field(default_factory=list, description="Tags for filtering")
    enabled: bool = Field(True, description="Whether this item is active")


class ChecklistClaimAction(BaseModel):
    """Claim/release request payload."""

    host_id: Optional[int] = Field(None, description="Host-specific scope; null for global scope")
    lease_seconds: Optional[int] = Field(
        None,
        ge=30,
        le=86400,
        description="Optional claim lease duration in seconds",
    )


class ChecklistClaimInfo(BaseModel):
    """Active claim metadata for checklist item."""

    claim_id: Optional[int] = None
    claimed_by_user_id: Optional[int] = None
    claimed_by_username: Optional[str] = None
    host_id: Optional[int] = None
    claimed_at: Optional[datetime] = None
    lease_expires_at: Optional[datetime] = None
    is_active: bool = False


class ChecklistItemCreate(ChecklistItemBase):
    """Schema for creating a checklist item."""
    group_id: int = Field(..., description="Parent group ID")
    order_index: Optional[int] = Field(None, description="Display order")


class ChecklistItemUpdate(BaseModel):
    """Schema for updating a checklist item."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    command_template: Optional[str] = None
    output_regex: Optional[Dict[str, str]] = None
    variables: Optional[Dict[str, Any]] = None
    input_definitions: Optional[Dict[str, Any]] = None
    storage_policy: Optional[Dict[str, Any]] = None
    parameter_schema: Optional[Dict[str, Any]] = None
    target_filter: Optional[Dict[str, Any]] = None
    timeout: Optional[int] = Field(None, ge=1)
    alert_patterns: Optional[Dict[str, List[str]]] = None
    tags: Optional[List[str]] = None
    enabled: Optional[bool] = None
    order_index: Optional[int] = None


class ChecklistItemResponse(ChecklistItemBase):
    """Schema for checklist item response."""
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    group_id: int
    order_index: int
    created_at: datetime
    updated_at: datetime
    
    # Execution info
    has_successful_execution: bool = False
    execution_count: int = 0
    latest_execution_status: Optional[str] = None
    latest_execution_at: Optional[datetime] = None
    latest_execution_id: Optional[int] = None
    
    # State
    is_trashed: bool = False
    claim: Optional[ChecklistClaimInfo] = None


class ChecklistGroupBase(BaseModel):
    """Base checklist group schema."""
    name: str = Field(..., min_length=1, max_length=255, description="Group name")
    description: Optional[str] = Field(None, description="Group description")
    icon: Optional[str] = Field(None, max_length=50, description="Icon identifier")
    color: Optional[str] = Field(None, max_length=20, description="Color code")
    collapsed: bool = Field(False, description="Whether group is collapsed in UI")
    is_template: bool = Field(False, description="Whether this is a reusable template")
    is_default_import: bool = Field(False, description="Auto-import on project create")
    requires_auth: bool = Field(False, description="Domain-scoped (per-domain) if True, global if False")
    speed_profile: str = Field("default", pattern="^(stealth|default|fast)$", description="Scan speed profile")


class ChecklistGroupCreate(ChecklistGroupBase):
    """Schema for creating a checklist group."""
    project_id: Optional[int] = Field(None, description="Parent project ID (None for global template)")
    order_index: Optional[int] = Field(None, description="Display order")


class ChecklistGroupUpdate(BaseModel):
    """Schema for updating a checklist group."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None
    collapsed: Optional[bool] = None
    order_index: Optional[int] = None
    is_template: Optional[bool] = None
    is_default_import: Optional[bool] = None
    requires_auth: Optional[bool] = None
    speed_profile: Optional[str] = Field(None, pattern="^(stealth|default|fast)$")


class ChecklistGroupResponse(ChecklistGroupBase):
    """Schema for checklist group response."""
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    project_id: Optional[int]
    order_index: int
    created_at: datetime
    
    # AD Domain scope
    ad_domain_id: Optional[int] = None
    ad_domain_name: Optional[str] = None
    
    # Items
    items: List[ChecklistItemResponse] = []
    
    # Stats
    completion_stats: dict = Field(default_factory=lambda: {"total": 0, "completed": 0, "percentage": 0})
