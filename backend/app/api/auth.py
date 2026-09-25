"""Authentication API endpoints."""
import time
from datetime import datetime
from typing import Dict, List

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import create_access_token, get_current_user, hash_password, verify_password
from app.database import get_db
from app.models.user import User
from app.schemas.auth import ChangePasswordRequest, TokenResponse, UserLogin, UserResponse

router = APIRouter()

_LOGIN_ATTEMPTS: Dict[str, List[float]] = {}
_LOGIN_WINDOW_SECONDS = 15 * 60
_LOGIN_MAX_FAILURES = 10
_LOGIN_TRACKED_KEYS = 10_000


def _login_allowed(key: str) -> bool:
    now = time.monotonic()
    hits = [t for t in _LOGIN_ATTEMPTS.get(key, []) if now - t < _LOGIN_WINDOW_SECONDS]
    _LOGIN_ATTEMPTS[key] = hits
    return len(hits) < _LOGIN_MAX_FAILURES


def _record_login_failure(key: str) -> None:
    _LOGIN_ATTEMPTS.setdefault(key, []).append(time.monotonic())
    if len(_LOGIN_ATTEMPTS) > _LOGIN_TRACKED_KEYS:
        _LOGIN_ATTEMPTS.pop(next(iter(_LOGIN_ATTEMPTS)))


@router.post("/login", response_model=TokenResponse)
async def login_user(data: UserLogin, request: Request, db: AsyncSession = Depends(get_db)):
    """Authenticate operator and return bearer token."""
    username_or_email = data.username.strip()
    client_key = request.client.host if request.client else "unknown"
    if not _login_allowed(client_key):
        raise HTTPException(status_code=429, detail="Too many login attempts",
                            headers={"Retry-After": str(_LOGIN_WINDOW_SECONDS)})

    result = await db.execute(
        select(User).where(
            or_(
                User.username == username_or_email,
                User.email == username_or_email.lower(),
            ),
            User.is_active.is_(True),
        )
    )
    user = result.scalar_one_or_none()
    if not user or not verify_password(data.password, user.password_hash):
        _record_login_failure(client_key)
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid credentials")

    user.last_login_at = datetime.utcnow()
    await db.flush()

    token = create_access_token(user)
    return TokenResponse(
        access_token=token,
        expires_in_seconds=8 * 60 * 60,
        user=UserResponse.model_validate(user),
    )


@router.get("/me", response_model=UserResponse)
async def get_me(user: User = Depends(get_current_user)):
    """Return currently authenticated user profile."""
    return UserResponse.model_validate(user)


@router.post("/change-password", response_model=UserResponse)
async def change_password(
    data: ChangePasswordRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Change own password. Clears the must_change_password flag."""
    if not verify_password(data.current_password, user.password_hash):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    user.password_hash = hash_password(data.new_password)
    user.must_change_password = False
    await db.flush()
    await db.refresh(user)
    return UserResponse.model_validate(user)
