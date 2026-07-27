"""User management API endpoints (admin only)."""
from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import get_current_admin, hash_password
from app.core.sync import sync_service
from app.database import get_db
from app.models.user import User
from app.schemas.auth import (
    ResetPasswordRequest,
    UserCreateAdmin,
    UserResponse,
    UserUpdateAdmin,
)


def _user_sync_payload(user: User) -> dict:
    return {
        "public_id": user.public_id,
        "username": user.username,
        "email": user.email,
        "password_hash": user.password_hash,
        "is_active": user.is_active,
        "role": user.role,
        "must_change_password": user.must_change_password,
        "updated_at": user.updated_at.isoformat() if user.updated_at else None,
    }

router = APIRouter()


@router.get("", response_model=List[UserResponse])
async def list_users(
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all users."""
    result = await db.execute(select(User).order_by(User.created_at.asc()))
    users = result.scalars().all()
    return [UserResponse.model_validate(u) for u in users]


@router.post("", response_model=UserResponse, status_code=201)
async def create_user(
    data: UserCreateAdmin,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create a new user (admin only)."""
    username = data.username.strip()
    result = await db.execute(select(User).where(User.username == username))
    if result.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Username already exists")

    email = (data.email or f"{username}@local").strip().lower()

    user = User(
        username=username,
        email=email,
        password_hash=hash_password(data.password),
        role=data.role,
        is_active=True,
        must_change_password=True,
    )
    db.add(user)
    await db.flush()
    await db.refresh(user)
    await sync_service.record_event(
        db,
        entity_type="users",
        entity_public_id=user.public_id,
        operation="create",
        payload=_user_sync_payload(user),
    )
    return UserResponse.model_validate(user)


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: int,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get a single user by ID."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return UserResponse.model_validate(user)


@router.put("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: int,
    data: UserUpdateAdmin,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update user fields (admin only). Cannot demote self from admin."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.id == admin.id and data.role is not None and data.role != "admin":
        raise HTTPException(status_code=400, detail="Cannot demote yourself from admin")

    if data.is_active is not None:
        if user.id == admin.id and not data.is_active:
            raise HTTPException(status_code=400, detail="Cannot deactivate yourself")
        user.is_active = data.is_active

    if data.role is not None:
        user.role = data.role

    if data.email is not None:
        user.email = data.email.strip().lower()

    await db.flush()
    await db.refresh(user)
    await sync_service.record_event(
        db,
        entity_type="users",
        entity_public_id=user.public_id,
        operation="update",
        payload=_user_sync_payload(user),
    )
    return UserResponse.model_validate(user)


@router.delete("/{user_id}", status_code=200)
async def delete_user(
    user_id: int,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Deactivate a user (admin only). Cannot deactivate self."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")

    user.is_active = False
    await db.flush()
    await db.refresh(user)
    await sync_service.record_event(
        db,
        entity_type="users",
        entity_public_id=user.public_id,
        operation="update",
        payload=_user_sync_payload(user),
    )
    return {"detail": f"User '{user.username}' deactivated"}


@router.post("/{user_id}/reset-password", response_model=UserResponse)
async def reset_user_password(
    user_id: int,
    data: ResetPasswordRequest,
    admin: User = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db),
):
    """Reset a user's password (admin only). Forces password change on next login."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.password_hash = hash_password(data.new_password)
    user.must_change_password = True
    await db.flush()
    await db.refresh(user)
    await sync_service.record_event(
        db,
        entity_type="users",
        entity_public_id=user.public_id,
        operation="update",
        payload=_user_sync_payload(user),
    )
    return UserResponse.model_validate(user)
