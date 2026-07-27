"""Collaboration helpers: claim ownership and duplicate prevention."""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.checklist import ChecklistItem
from app.models.checklist_claim import ChecklistClaim
from app.models.execution import Execution, ExecutionStatus
from app.models.user import ProjectMember, User
from app.core.sync import sync_service, claim_sync_payload


class ClaimConflictError(Exception):
    """Raised when an item scope is currently claimed by another user."""

    def __init__(self, claim: ChecklistClaim):
        self.claim = claim
        super().__init__("Checklist item is claimed by another operator")


class DuplicateExecutionError(Exception):
    """Raised when an equivalent execution is already pending/running."""

    def __init__(self, existing_execution_id: int):
        self.existing_execution_id = existing_execution_id
        super().__init__(f"Execution already active: {existing_execution_id}")


class ProjectAccessError(Exception):
    """Raised when user is not a member of project scope."""

    pass


def scope_key_for_host(host_id: Optional[int]) -> str:
    """Convert host scope into deterministic key for uniqueness."""
    return "global" if host_id is None else f"host:{host_id}"


def _is_claim_expired(claim: ChecklistClaim, now: datetime) -> bool:
    if claim.lease_expires_at is None:
        return False
    return claim.lease_expires_at <= now


async def get_item_project_id(db: AsyncSession, item_id: int) -> int:
    """Resolve item -> project id from checklist group."""
    result = await db.execute(
        select(ChecklistItem)
        .where(ChecklistItem.id == item_id)
        .options(selectinload(ChecklistItem.group))
    )
    item = result.scalar_one_or_none()
    if item is None or item.group is None or item.group.project_id is None:
        raise ValueError("Checklist item not found in project scope")
    return item.group.project_id


async def claim_item_scope(
    db: AsyncSession,
    *,
    item_id: int,
    host_id: Optional[int],
    user: User,
    lease_seconds: Optional[int] = None,
    force_takeover: bool = False,
) -> ChecklistClaim:
    """Claim or takeover an item scope."""
    project_id = await get_item_project_id(db, item_id)
    membership = await db.execute(
        select(ProjectMember.id).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user.id,
            ProjectMember.deleted_at.is_(None),
        )
    )
    if membership.scalar_one_or_none() is None:
        raise ProjectAccessError("User is not a project member")
    now = datetime.utcnow()
    lease = now + timedelta(seconds=lease_seconds or settings.DEFAULT_CLAIM_LEASE_SECONDS)
    scope_key = scope_key_for_host(host_id)

    claim_result = await db.execute(
        select(ChecklistClaim)
        .where(
            ChecklistClaim.project_id == project_id,
            ChecklistClaim.item_id == item_id,
            ChecklistClaim.host_scope_key == scope_key,
        )
        .options(selectinload(ChecklistClaim.claimed_by_user))
    )
    claim = claim_result.scalar_one_or_none()

    if claim is None:
        claim = ChecklistClaim(
            project_id=project_id,
            item_id=item_id,
            host_id=host_id,
            host_scope_key=scope_key,
            claimed_by_user_id=user.id,
            claimed_at=now,
            lease_expires_at=lease,
            is_active=True,
            deleted_at=None,
            version=1,
        )
        db.add(claim)
        await db.flush()
        await db.refresh(claim, ["claimed_by_user"])
        try:
            payload = await claim_sync_payload(db, claim)
            await sync_service.record_event(
                db,
                entity_type="checklist_claims",
                entity_public_id=claim.public_id,
                operation="claim",
                payload=payload,
            )
        except Exception as e:
            print(f"[Collaboration] Sync record (claim) failed: {e}")
        return claim

    is_expired = _is_claim_expired(claim, now)
    already_mine = claim.claimed_by_user_id == user.id
    is_available = not claim.is_active or is_expired or claim.claimed_by_user_id is None

    if not already_mine and not is_available and not force_takeover:
        raise ClaimConflictError(claim)

    if not already_mine:
        claim.version += 1

    claim.host_id = host_id
    claim.claimed_by_user_id = user.id
    claim.claimed_at = now
    claim.lease_expires_at = lease
    claim.is_active = True
    claim.deleted_at = None
    await db.flush()
    await db.refresh(claim, ["claimed_by_user"])
    try:
        operation = "takeover" if not already_mine else "claim"
        payload = await claim_sync_payload(db, claim)
        await sync_service.record_event(
            db,
            entity_type="checklist_claims",
            entity_public_id=claim.public_id,
            operation=operation,
            payload=payload,
        )
    except Exception as e:
        print(f"[Collaboration] Sync record (claim update) failed: {e}")
    return claim


async def release_item_scope(
    db: AsyncSession,
    *,
    item_id: int,
    host_id: Optional[int],
    user: User,
    force: bool = False,
) -> Optional[ChecklistClaim]:
    """Release active claim for scope."""
    project_id = await get_item_project_id(db, item_id)
    membership = await db.execute(
        select(ProjectMember.id).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user.id,
            ProjectMember.deleted_at.is_(None),
        )
    )
    if membership.scalar_one_or_none() is None:
        raise ProjectAccessError("User is not a project member")
    scope_key = scope_key_for_host(host_id)
    result = await db.execute(
        select(ChecklistClaim)
        .where(
            ChecklistClaim.project_id == project_id,
            ChecklistClaim.item_id == item_id,
            ChecklistClaim.host_scope_key == scope_key,
        )
        .options(selectinload(ChecklistClaim.claimed_by_user))
    )
    claim = result.scalar_one_or_none()
    if claim is None:
        return None

    if claim.claimed_by_user_id not in (None, user.id) and not force:
        raise ClaimConflictError(claim)

    claim.is_active = False
    claim.claimed_by_user_id = None
    claim.claimed_at = None
    claim.lease_expires_at = None
    claim.version += 1
    claim.deleted_at = datetime.utcnow()
    await db.flush()
    await db.refresh(claim)
    try:
        payload = await claim_sync_payload(db, claim)
        await sync_service.record_event(
            db,
            entity_type="checklist_claims",
            entity_public_id=claim.public_id,
            operation="release",
            payload=payload,
        )
    except Exception as e:
        print(f"[Collaboration] Sync record (release) failed: {e}")
    return claim


async def ensure_no_active_execution(
    db: AsyncSession,
    *,
    item_id: int,
    host_id: Optional[int],
) -> None:
    """Prevent duplicate pending/running executions for same item+host."""
    base_query = select(Execution).where(
        Execution.item_id == item_id,
        Execution.status.in_([ExecutionStatus.PENDING, ExecutionStatus.RUNNING]),
    )
    if host_id is None:
        base_query = base_query.where(Execution.host_id.is_(None))
    else:
        base_query = base_query.where(Execution.host_id == host_id)

    result = await db.execute(base_query.order_by(Execution.created_at.desc()))
    existing = result.scalar_one_or_none()
    if existing is not None:
        raise DuplicateExecutionError(existing.id)
