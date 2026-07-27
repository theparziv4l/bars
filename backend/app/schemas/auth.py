"""Schemas for authentication and user membership."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class UserLogin(BaseModel):
    """Login payload."""

    username: str = Field(..., min_length=1, max_length=255)
    password: str = Field(..., min_length=1, max_length=256)


class UserResponse(BaseModel):
    """User response model."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    public_id: str
    username: str
    email: str
    is_active: bool
    role: str
    must_change_password: bool
    created_at: datetime
    last_login_at: Optional[datetime] = None


class TokenResponse(BaseModel):
    """Bearer token response."""

    access_token: str
    token_type: str = "bearer"
    expires_in_seconds: int
    user: UserResponse


class ChangePasswordRequest(BaseModel):
    """Change own password payload."""

    current_password: str = Field(..., min_length=1, max_length=256)
    new_password: str = Field(..., min_length=8, max_length=256)


class UserCreateAdmin(BaseModel):
    """Admin-only: create a new user."""

    username: str = Field(..., min_length=3, max_length=80)
    password: str = Field(..., min_length=1, max_length=256)
    role: str = Field(default="operator", pattern=r"^(admin|operator)$")
    email: Optional[str] = Field(default=None, max_length=255)


class UserUpdateAdmin(BaseModel):
    """Admin-only: update user fields."""

    is_active: Optional[bool] = None
    role: Optional[str] = Field(default=None, pattern=r"^(admin|operator)$")
    email: Optional[str] = Field(default=None, max_length=255)


class ResetPasswordRequest(BaseModel):
    """Admin-only: reset a user's password."""

    new_password: str = Field(..., min_length=8, max_length=256)


class ProjectMemberCreate(BaseModel):
    """Project membership assignment payload."""

    username_or_email: str = Field(..., min_length=1, max_length=255)


class ProjectMemberResponse(BaseModel):
    """Project member payload."""

    id: int
    public_id: str
    project_id: int
    user_id: int
    username: str
    email: str
    created_at: datetime
