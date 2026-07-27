"""Pydantic schemas for Flow entities."""
from datetime import datetime
from typing import Optional, List, Dict, Any, Union
from pydantic import BaseModel, Field, ConfigDict


class FlowStepBase(BaseModel):
    """Base flow step schema."""
    checklist_item_id: int = Field(..., description="Checklist item to execute")
    input_mapping: Dict[str, Union[str, Dict[str, Any]]] = Field(default_factory=dict, description="Variable mapping from previous steps")
    condition: Optional[str] = Field(None, description="Condition for executing this step")
    on_failure: str = Field("stop", pattern="^(stop|continue|skip_remaining)$", description="Failure handling")
    timeout_override: Optional[int] = Field(None, ge=1, description="Custom timeout for this step")
    target_mode: str = Field("inherit", pattern="^(inherit|all|filtered|single)$", description="How to resolve targets for this step")
    target_filter: Dict[str, Any] = Field(default_factory=dict, description="Tag filter when target_mode=filtered, e.g. {'tags': ['web']}")
    ui_position: Optional[Dict[str, float]] = Field(default_factory=dict, description="UI coordinates {'x': 0, 'y': 0}")


class FlowStepCreate(FlowStepBase):
    """Schema for creating a flow step."""
    order_index: Optional[int] = Field(None, description="Execution order")


class FlowStepResponse(FlowStepBase):
    """Schema for flow step response."""
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    flow_id: int
    order_index: int
    
    # Item info for display
    item_name: Optional[str] = None
    item_command: Optional[str] = None
    
    # Parameter mapping support
    output_regex_keys: List[str] = Field(default_factory=list, description="Available output variable keys")
    variables: Dict[str, Any] = Field(default_factory=dict, description="Default input variables")

    # Target overrides (inherited from FlowStepBase but included explicitly for clarity)
    target_mode: str = "inherit"
    target_filter: Dict[str, Any] = {}


class FlowBase(BaseModel):
    """Base flow schema."""
    name: str = Field(..., min_length=1, max_length=255, description="Flow name")
    description: Optional[str] = Field(None, description="Flow description")
    is_template: bool = Field(False, description="Whether this is a reusable template")
    is_default_import: bool = Field(False, description="Auto-import on project create")
    requires_auth: bool = Field(False, description="Domain-scoped (per-domain) if True, global if False")
    tags: List[str] = Field(default_factory=list, description="Tags for categorization")


class FlowCreate(FlowBase):
    """Schema for creating a flow."""
    project_id: Optional[int] = Field(None, description="Parent project ID (None for global template)")
    steps: List[FlowStepCreate] = Field(default_factory=list, description="Flow steps")


class FlowUpdate(BaseModel):
    """Schema for updating a flow."""
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    is_template: Optional[bool] = None
    is_default_import: Optional[bool] = None
    requires_auth: Optional[bool] = None
    tags: Optional[List[str]] = None
    steps: Optional[List[FlowStepCreate]] = None
    flow_definition: Optional[Dict[str, Any]] = None


class FlowResponse(FlowBase):
    """Schema for flow response."""
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    project_id: Optional[int]
    flow_definition: Dict[str, Any] = {}
    created_at: datetime
    updated_at: datetime

    ad_domain_id: Optional[int] = None
    ad_domain_name: Optional[str] = None
    
    # Steps
    steps: List[FlowStepResponse] = []


class FlowExecutionCreate(BaseModel):
    """Schema for executing a flow."""
    flow_id: Optional[int] = Field(None, description="Flow ID to execute (optional if in URL)")
    host_id: Optional[int] = Field(None, description="Target host ID (single host)")
    host_ids: List[int] = Field(default_factory=list, description="Multiple target host IDs")
    target: Optional[str] = Field(None, description="Single target string (IP/hostname)")
    targets: List[str] = Field(default_factory=list, description="Multiple targets (IPs/hostnames)")
    variables: Dict[str, Any] = Field(default_factory=dict, description="Initial variables")


class FlowExecutionResponse(BaseModel):
    """Schema for flow execution record response."""
    model_config = ConfigDict(from_attributes=True)

    id: str
    flow_id: int
    status: str
    variables: Dict[str, Any] = {}
    target: Optional[str] = None
    targets: List[str] = []
    host_id: Optional[int] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime
    error: Optional[str] = None


class FlowExecutionStatus(BaseModel):
    """Schema for flow execution status."""
    flow_execution_id: str
    flow_id: int
    status: str = Field(..., description="pending, running, completed, failed, cancelled")
    current_step: int = 0
    total_steps: int = 0
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    target: Optional[str] = None
    targets: List[str] = []
    host_id: Optional[int] = None

    # Step statuses
    step_statuses: List[Dict[str, Any]] = []

    # Accumulated outputs
    outputs: Dict[str, Any] = {}

    # Errors if any
    error: Optional[str] = None
