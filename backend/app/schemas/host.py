"""Pydantic schemas for Host and Service."""
from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field, ConfigDict


class ServiceBase(BaseModel):
    """Base service schema."""
    port: int = Field(..., ge=0, le=65535, description="Port number (0 for ICMP/unknown)")
    protocol: str = Field("tcp", pattern="^(tcp|udp)$", description="Protocol")
    name: Optional[str] = Field(None, max_length=100, description="Service name")
    version: Optional[str] = Field(None, max_length=255, description="Service version")
    product: Optional[str] = Field(None, max_length=255, description="Product name")
    state: str = Field("open", description="Port state")
    banner: Optional[str] = Field(None, description="Service banner")
    ssl: bool = Field(False, description="Whether SSL/TLS is used")
    extra_data: dict = Field(default_factory=dict)


class ServiceCreate(ServiceBase):
    """Schema for creating a service."""
    pass


class ServiceResponse(ServiceBase):
    """Schema for service response."""
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    host_id: int
    discovered_at: datetime
    display_name: str


class HostBase(BaseModel):
    """Base host schema."""
    ip_address: Optional[str] = Field(None, max_length=45, description="IPv4 or IPv6 address")
    hostname: Optional[str] = Field(None, max_length=255, description="Short hostname")
    fqdn: Optional[str] = Field(None, max_length=255, description="Fully qualified domain name")
    os_info: Optional[str] = Field(None, max_length=255, description="OS information")
    status: str = Field("unknown", description="Host status (up, down, unknown)")
    notes: Optional[str] = Field(None, description="Notes about the host")
    extra_data: dict = Field(default_factory=dict)
    tags: List[str] = Field(default_factory=list, description="Service-type tags for target filtering")
    excluded: bool = Field(False, description="Out of scope: no execution path may target this host")


class HostCreate(HostBase):
    """Schema for creating a host."""
    project_id: int = Field(..., description="Parent project ID")


class HostUpdate(BaseModel):
    """Schema for updating a host."""
    ip_address: Optional[str] = Field(None, max_length=45)
    hostname: Optional[str] = Field(None, max_length=255)
    fqdn: Optional[str] = Field(None, max_length=255)
    os_info: Optional[str] = Field(None, max_length=255)
    status: Optional[str] = None
    notes: Optional[str] = None
    extra_data: Optional[dict] = None
    tags: Optional[List[str]] = None
    excluded: Optional[bool] = None


class HostResponse(HostBase):
    """Schema for host response."""
    model_config = ConfigDict(from_attributes=True)
    
    id: int
    project_id: int
    discovered_at: datetime
    updated_at: datetime
    display_name: str
    
    # Related data
    services: List[ServiceResponse] = []
    service_count: int = 0
    execution_count: int = 0
    file_count: int = 0


class HostBulkScopeRequest(BaseModel):
    """Mark a set of hosts in or out of scope."""
    host_ids: List[int] = Field(..., min_length=1, description="Hosts to update")
    excluded: bool = Field(..., description="True = out of scope, False = back in scope")


class HostListResponse(BaseModel):
    """Schema for paginated host list."""
    items: List[HostResponse]
    total: int
    page: int
    per_page: int


# --- Script scan (NSE) schemas ---


class GenerateScriptScanRequest(BaseModel):
    """Request to generate Service Scan checklist items for a project."""
    service_names: Optional[List[str]] = Field(None, description="Only include these services (default: all discovered)")
    timing: int = Field(4, ge=0, le=5, description="Nmap timing template (0-5)")
    extra_args: Optional[str] = Field(None, description="Extra nmap arguments")


class GenerateScriptScanResponse(BaseModel):
    """Response after generating Service Scan items."""
    group_id: int = Field(..., description="Created or updated checklist group ID")
    items: List[dict] = Field(default_factory=list, description="Created/updated checklist items with scripts, ports, tag, host_count")
    skipped_services: List[str] = Field(default_factory=list, description="Service names with no NSE mapping")


class HostScriptScanRequest(BaseModel):
    """Request to run NSE script scan for a single host."""
    service_names: Optional[List[str]] = Field(None, description="Only scan these services (default: all discovered)")
    timing: int = Field(4, ge=0, le=5, description="Nmap timing template (0-5)")
    extra_args: Optional[str] = Field(None, description="Extra nmap arguments")


class HostScriptScanResponse(BaseModel):
    """Response after starting a host script scan execution."""
    execution_id: int = Field(..., description="Execution record ID")
    command: str = Field(..., description="Command being run")
    host_id: int = Field(..., description="Host ID")
