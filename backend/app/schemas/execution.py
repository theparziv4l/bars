"""Pydantic schemas for Execution entities."""
from datetime import datetime
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field, ConfigDict


class ExecutionOutputResponse(BaseModel):
    """Schema for execution output response."""
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    execution_id: int
    key: str
    value: str
    data_type: str


class ExecutionBase(BaseModel):
    """Base execution schema."""
    variables_used: Dict[str, Any] = Field(default_factory=dict, description="Variables used in execution")


class ExecutionCreate(BaseModel):
    """Schema for creating/starting an execution."""
    item_id: int = Field(..., description="Checklist item ID")
    host_id: Optional[int] = Field(None, description="Target host ID")
    variables: Dict[str, Any] = Field(default_factory=dict, description="Variable values")
    
    # Flow context (optional)
    flow_execution_id: Optional[str] = Field(None, description="Flow execution ID if part of flow")
    flow_step_index: Optional[int] = Field(None, description="Step index in flow")
    
    # Validation/Overrides
    command_override: Optional[str] = Field(None, description="Temporary command override for this execution")
    force_takeover: bool = Field(False, description="Force takeover conflicting claim before start")


class ExecutionResponse(BaseModel):
    """Schema for execution response."""
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    item_id: int
    host_id: Optional[int]
    version: int
    status: str
    command: str
    variables_used: Dict[str, Any]
    
    # Output
    stdout: Optional[str] = None
    stderr: Optional[str] = None
    exit_code: Optional[int] = None
    parsed_output: Dict[str, Any] = {}
    alerts_triggered: List[Any] = []
    
    # Timestamps
    created_at: datetime
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    
    # Computed
    duration: Optional[float] = None
    is_success: bool = False
    
    # Flow context
    flow_execution_id: Optional[str] = None
    flow_step_index: Optional[int] = None
    started_by_user_id: Optional[int] = None
    
    # Related
    outputs: List[ExecutionOutputResponse] = []
    
    # Item info (for display)
    item_name: Optional[str] = None
    host_display_name: Optional[str] = None


class ExecutionListResponse(BaseModel):
    """Schema for paginated execution list."""
    items: List[ExecutionResponse]
    total: int
    page: int
    per_page: int


class ExecutionStreamMessage(BaseModel):
    """Schema for WebSocket execution stream messages."""
    execution_id: int
    event: str = Field(..., description="Event type: output, status, alert, complete")
    data: Any = Field(..., description="Event data")
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class BulkExecutionCreate(BaseModel):
    """Schema for bulk execution of multiple items."""
    item_ids: List[int] = Field(..., min_length=1, description="Checklist item IDs to execute")
    host_id: Optional[int] = Field(None, description="Target host ID")
    variables: Dict[str, Any] = Field(default_factory=dict, description="Shared variables")
    sequential: bool = Field(False, description="Execute sequentially vs parallel")
