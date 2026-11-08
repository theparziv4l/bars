"""Execution API endpoints."""
from datetime import datetime
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, Query, BackgroundTasks
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, text, inspect as sa_inspect
from sqlalchemy.orm import selectinload

from app.core.auth import get_current_user_optional
from app.core.collaboration import (
    ClaimConflictError,
    DuplicateExecutionError,
    ProjectAccessError,
    claim_item_scope,
    ensure_no_active_execution,
)
from app.core.sync import sync_service, execution_sync_payload
from app.config import settings
from app.database import get_db
from app.models.checklist import ChecklistItem
from app.models.execution import Execution, ExecutionStatus
from app.models.host import Host
from app.models.user import User
from app.schemas.execution import ExecutionCreate, ExecutionResponse, ExecutionListResponse, BulkExecutionCreate
from app.core.orchestrator import task_orchestrator
from app.core.notifications import notification_manager
from app.core.utils import utcnow

router = APIRouter()


async def _reject_excluded_host(db: AsyncSession, host_id: Optional[int]) -> None:
    """Refuse a host-scoped execution against an out-of-scope host.

    The orchestrator fails the same case, but only after an execution record exists;
    refusing at the API keeps a stale tab or a raw POST from littering the timeline
    with failures.
    """
    if not host_id:
        return
    row = (await db.execute(select(Host.excluded).where(Host.id == host_id))).first()
    if row and row[0]:
        raise HTTPException(
            status_code=400,
            detail="Host is excluded from scope. Re-include it in the Assets tab to run against it.",
        )


async def _check_host_scope(
    db: AsyncSession, host_id: Optional[int], item_project_id: Optional[int], user: Optional[User]
) -> None:
    """The host must be live and belong to the item's project (template items: the
    caller must be a member of the host's project)."""
    if not host_id:
        return
    row = (await db.execute(
        select(Host.project_id).where(Host.id == host_id, Host.deleted_at.is_(None))
    )).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Host not found")
    if item_project_id is None:
        from app.api.projects import _require_project_member
        await _require_project_member(db, row[0], user)
    elif row[0] != item_project_id:
        raise HTTPException(status_code=400, detail="Host does not belong to the item's project")


async def _project_for_item(db: AsyncSession, item_id: int) -> Optional[int]:
    """Project owning a checklist item (None when item/group missing)."""
    row = (await db.execute(
        select(ChecklistItem).where(ChecklistItem.id == item_id)
        .options(selectinload(ChecklistItem.group))
    )).scalar_one_or_none()
    if row is None or row.group is None:
        return None
    return row.group.project_id


async def _project_for_execution(db: AsyncSession, execution_id: int) -> Optional[int]:
    row = (await db.execute(
        select(Execution).where(Execution.id == execution_id)
    )).scalar_one_or_none()
    if row is None:
        return None
    return await _project_for_item(db, row.item_id)


@router.get("", response_model=ExecutionListResponse)
async def list_executions(
    project_id: Optional[int] = None,
    item_id: Optional[int] = None,
    host_id: Optional[int] = None,
    status: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """List executions with filters."""
    if project_id:
        from app.api.projects import _require_project_member
        await _require_project_member(db, project_id, current_user)
    query = select(Execution).options(selectinload(Execution.outputs))
    
    if project_id:
        # Filter by project:
        # 1. Execution -> ChecklistItem -> Group -> Project
        # 2. Execution -> Host -> Project (if host exists)
        # We want executions that belong to this project context.
        from app.models.checklist import ChecklistItem, ChecklistGroup
        from app.models.host import Host
        
        query = query.join(Execution.checklist_item).join(ChecklistItem.group).outerjoin(Execution.host)
        
        query = query.where(
            (ChecklistGroup.project_id == project_id) | 
            (Host.project_id == project_id)
        )

    if item_id:
        query = query.where(Execution.item_id == item_id)
    if host_id:
        query = query.where(Execution.host_id == host_id)
    if status:
        try:
            query = query.where(Execution.status == ExecutionStatus(status))
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status '{status}'. Valid values: {[s.value for s in ExecutionStatus]}",
            )

    
    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar()
    
    query = query.order_by(Execution.created_at.desc()).offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(query)
    executions = result.scalars().all()
    
    return ExecutionListResponse(
        items=[_execution_to_response(e) for e in executions],
        total=total, page=page, per_page=per_page
    )


@router.post("", response_model=ExecutionResponse, status_code=201)
async def start_execution(
    data: ExecutionCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Start a new execution (returns immediately, runs in background)."""
    await _reject_excluded_host(db, data.host_id)
    _start_pid = await _project_for_item(db, data.item_id)
    if _start_pid is not None:
        from app.api.projects import _require_project_member
        await _require_project_member(db, _start_pid, current_user)
    await _check_host_scope(db, data.host_id, _start_pid, current_user)

    if settings.CLAIMS_ENABLED and current_user is not None:
        try:
            await claim_item_scope(
                db,
                item_id=data.item_id,
                host_id=data.host_id,
                user=current_user,
                force_takeover=data.force_takeover,
            )
        except ProjectAccessError as err:
            raise HTTPException(status_code=403, detail=str(err)) from err
        except ClaimConflictError as err:
            claim = err.claim
            raise HTTPException(
                status_code=409,
                detail={
                    "reason": "claimed_by_other",
                    "item_id": data.item_id,
                    "host_id": data.host_id,
                    "claimed_by_user_id": claim.claimed_by_user_id,
                    "claimed_by_username": claim.claimed_by_user.username if claim.claimed_by_user else None,
                    "lease_expires_at": claim.lease_expires_at.isoformat() if claim.lease_expires_at else None,
                },
            ) from err

    try:
        await ensure_no_active_execution(db, item_id=data.item_id, host_id=data.host_id)
    except DuplicateExecutionError as err:
        raise HTTPException(
            status_code=409,
            detail={
                "reason": "duplicate_running_execution",
                "existing_execution_id": err.existing_execution_id,
                "item_id": data.item_id,
                "host_id": data.host_id,
            },
        ) from err

    try:
        execution = await task_orchestrator.create_execution_record(
            db=db,
            item_id=data.item_id,
            host_id=data.host_id,
            variables=data.variables,
            flow_execution_id=data.flow_execution_id,
            flow_step_index=data.flow_step_index,
            command_override=data.command_override,
            started_by_user_id=current_user.id if current_user else None,
        )
    except ValueError as err:
        if "already active for item/host scope" in str(err):
            raise HTTPException(
                status_code=409,
                detail={
                    "reason": "duplicate_running_execution",
                    "message": str(err),
                    "item_id": data.item_id,
                    "host_id": data.host_id,
                },
            ) from err
        raise

    # Commit before scheduling background task so it can find the record in its own session
    payload = await execution_sync_payload(db, execution)
    await sync_service.record_event(
        db,
        entity_type="executions",
        entity_public_id=execution.public_id,
        operation="create",
        payload=payload,
    )
    await db.commit()

    background_tasks.add_task(
        task_orchestrator.run_execution_background,
        execution.id,
        data.item_id,
        data.host_id,
        data.variables,
        data.flow_execution_id,
        data.flow_step_index,
        data.command_override
    )

    return _execution_to_response(execution)


@router.post("/bulk")
async def start_bulk_execution(
    data: BulkExecutionCreate,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Execute multiple checklist items."""
    await _reject_excluded_host(db, data.host_id)
    from app.api.projects import _require_project_member
    for _iid in dict.fromkeys(data.item_ids):
        _pid = await _project_for_item(db, _iid)
        if _pid is not None:
            await _require_project_member(db, _pid, current_user)
        await _check_host_scope(db, data.host_id, _pid, current_user)

    executions = await task_orchestrator.execute_bulk(
        db=db,
        item_ids=data.item_ids,
        host_id=data.host_id,
        variables=data.variables,
        sequential=data.sequential
    )
    return {"executions": [_execution_to_response(e) for e in executions]}


@router.get("/running")
async def get_running_executions(
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Get currently running execution IDs."""
    return {"running": await task_orchestrator.get_running_executions()}


@router.get("/{execution_id}", response_model=ExecutionResponse)
async def get_execution(
    execution_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Get execution details."""
    result = await db.execute(
        select(Execution).where(Execution.id == execution_id)
        .options(selectinload(Execution.outputs))
    )
    execution = result.scalar_one_or_none()
    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")
    _pid = await _project_for_item(db, execution.item_id)
    if _pid is not None:
        from app.api.projects import _require_project_member
        await _require_project_member(db, _pid, current_user)
    return _execution_to_response(execution)


@router.post("/{execution_id}/cancel")
async def cancel_execution(
    execution_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Cancel a running execution."""
    _row = (await db.execute(
        select(Execution).where(Execution.id == execution_id)
    )).scalar_one_or_none()
    if not _row:
        raise HTTPException(status_code=404, detail="Execution not found")
    _pid = await _project_for_item(db, _row.item_id)
    if _pid is not None:
        from app.api.projects import _require_project_member
        await _require_project_member(db, _pid, current_user)
    success = await task_orchestrator.cancel_execution(execution_id)
    if not success:
        result = await db.execute(
            select(Execution).where(Execution.id == execution_id)
        )
        execution = result.scalar_one_or_none()
        if not execution:
            raise HTTPException(status_code=404, detail="Execution not found")
        if execution.status in (ExecutionStatus.PENDING, ExecutionStatus.RUNNING):
            execution.status = ExecutionStatus.CANCELLED
            execution.completed_at = utcnow()
            execution.stderr = (execution.stderr or "") + "\nCancelled (stale execution)."
            await db.commit()
            await notification_manager.send_execution_status(
                execution_id, "cancelled",
                details={"reason": "stale_cleanup"},
                item_id=execution.item_id,
            )
            return {"status": "cancelled"}
        raise HTTPException(status_code=404, detail="Execution not running")
    return {"status": "cancelled"}


@router.post("/{execution_id}/cancel-batch")
async def cancel_batch_execution(
    execution_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Cancel a batch parent and all its PENDING/RUNNING children."""
    result = await db.execute(
        select(Execution).where(Execution.id == execution_id)
    )
    parent = result.scalar_one_or_none()
    if not parent:
        raise HTTPException(status_code=404, detail="Execution not found")
    _pid = await _project_for_item(db, parent.item_id)
    if _pid is not None:
        from app.api.projects import _require_project_member
        await _require_project_member(db, _pid, current_user)

    # Cancel parent via orchestrator (sets cancelled flag, kills running children in memory)
    await task_orchestrator.cancel_execution(execution_id)

    # DB cleanup: find all children by parent_execution_id in variables_used JSON
    children_query = select(Execution).where(
        Execution.status.in_([ExecutionStatus.PENDING, ExecutionStatus.RUNNING]),
        text("json_extract(executions.variables_used, '$.parent_execution_id') = :parent_id")
    ).params(parent_id=execution_id)
    children_result = await db.execute(children_query)
    children = children_result.scalars().all()

    cancelled_count = 0
    now = utcnow()
    for child in children:
        child.status = ExecutionStatus.CANCELLED
        child.completed_at = now
        child.stderr = (child.stderr or "") + "\nCancelled by batch kill."
        cancelled_count += 1

        await notification_manager.send_execution_status(
            child.id,
            "cancelled",
            details={"reason": "batch_kill"},
            item_id=child.item_id,
        )

    # Also mark the parent as cancelled if still active
    if parent.status in (ExecutionStatus.PENDING, ExecutionStatus.RUNNING):
        parent.status = ExecutionStatus.CANCELLED
        parent.completed_at = now
        parent.stderr = (parent.stderr or "") + "\nBatch execution killed by user."

    await db.commit()

    await notification_manager.send_execution_status(
        execution_id,
        "cancelled",
        details={"reason": "batch_kill", "children_cancelled": cancelled_count},
        item_id=parent.item_id,
    )

    return {"status": "cancelled", "children_cancelled": cancelled_count}


@router.delete("/{execution_id}", status_code=204)
async def delete_execution(
    execution_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Delete an execution record."""
    result = await db.execute(
        select(Execution).where(Execution.id == execution_id)
    )
    execution = result.scalar_one_or_none()
    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")
    _pid = await _project_for_item(db, execution.item_id)
    if _pid is not None:
        from app.api.projects import _require_project_member
        await _require_project_member(db, _pid, current_user)
    if execution.status in (ExecutionStatus.RUNNING, ExecutionStatus.PENDING):
        raise HTTPException(status_code=409, detail="Cannot delete a running or pending execution")
    payload = await execution_sync_payload(db, execution)
    await sync_service.record_event(
        db,
        entity_type="executions",
        entity_public_id=execution.public_id,
        operation="delete",
        payload=payload,
    )
    await db.delete(execution)
    await db.commit()
    return None


@router.get("/{execution_id}/output")
async def get_execution_output(
    execution_id: int,
    stream: str = "stdout",
    db: AsyncSession = Depends(get_db),
    current_user: Optional[User] = Depends(get_current_user_optional),
):
    """Get execution output."""
    result = await db.execute(select(Execution).where(Execution.id == execution_id))
    execution = result.scalar_one_or_none()
    if not execution:
        raise HTTPException(status_code=404, detail="Execution not found")
    _pid = await _project_for_item(db, execution.item_id)
    if _pid is not None:
        from app.api.projects import _require_project_member
        await _require_project_member(db, _pid, current_user)
    
    return {"output": execution.stdout if stream == "stdout" else execution.stderr}


def _execution_to_response(execution: Execution) -> ExecutionResponse:
    outputs_loaded = 'outputs' not in sa_inspect(execution).unloaded
    outputs = execution.outputs if outputs_loaded else []
    return ExecutionResponse(
        id=execution.id, item_id=execution.item_id, host_id=execution.host_id,
        version=execution.version, status=execution.status.value,
        command=execution.command, variables_used=execution.variables_used,
        stdout=execution.stdout, stderr=execution.stderr,
        exit_code=execution.exit_code, parsed_output=execution.parsed_output,
        alerts_triggered=execution.alerts_triggered,
        created_at=execution.created_at, started_at=execution.started_at,
        completed_at=execution.completed_at, duration=execution.duration,
        is_success=execution.is_success,
        flow_execution_id=execution.flow_execution_id,
        flow_step_index=execution.flow_step_index,
        started_by_user_id=execution.started_by_user_id,
        outputs=[{"id": o.id, "execution_id": o.execution_id, "key": o.key, "value": o.value, "data_type": o.data_type} for o in (outputs or [])]
    )
