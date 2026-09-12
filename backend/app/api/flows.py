"""Flow API endpoints."""
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse
import json
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, inspect as sa_inspect
from sqlalchemy.orm import selectinload, joinedload

from app.database import get_db
from app.models.flow import Flow, FlowStep
from app.models.flow_execution import FlowExecution
from app.models.checklist import ChecklistItem, ChecklistGroup
from app.core.sync import sync_service, flow_sync_payload, flow_step_sync_payload
from app.core.utils import merge_set_values
from app.schemas.flow import (
    FlowCreate, FlowUpdate, FlowResponse,
    FlowExecutionCreate, FlowExecutionResponse, FlowExecutionStatus,
)
from app.schemas.execution import ExecutionResponse

router = APIRouter()


@router.get("", response_model=List[FlowResponse])
async def list_flows(
    project_id: Optional[int] = None,
    is_template: bool = False,
    db: AsyncSession = Depends(get_db)
):
    """List flows for a project or global templates."""
    query = select(Flow)
    
    if project_id:
        query = query.where(Flow.project_id == project_id)
    else:
        # If no project_id, assume templates
        query = query.where(Flow.project_id.is_(None))
        
    if is_template:
        query = query.where(Flow.is_template == True)
        
    result = await db.execute(
        query
        .options(selectinload(Flow.steps).selectinload(FlowStep.checklist_item))
    )
    flows = result.scalars().all()

    domain_ids = {f.ad_domain_id for f in flows if getattr(f, "ad_domain_id", None)}
    domain_name_map: dict = {}
    if domain_ids:
        from app.models.ad_domain import ADDomain
        domain_result = await db.execute(
            select(ADDomain.id, ADDomain.name).where(ADDomain.id.in_(domain_ids))
        )
        domain_name_map = {r[0]: r[1] for r in domain_result.all()}
    for f in flows:
        f._ad_domain_name = domain_name_map.get(getattr(f, "ad_domain_id", None))

    return [_flow_to_response(f) for f in flows]


@router.post("", response_model=FlowResponse, status_code=201)
async def create_flow(data: FlowCreate, db: AsyncSession = Depends(get_db)):
    """Create a new flow."""
    flow = Flow(
        project_id=data.project_id,
        name=data.name,
        description=data.description,
        is_template=data.is_template,
        tags=data.tags,
        flow_definition={"steps": [s.model_dump() for s in data.steps]}
    )
    db.add(flow)
    await db.flush()
    
    for idx, step_data in enumerate(data.steps):
        step = FlowStep(
            flow_id=flow.id,
            checklist_item_id=step_data.checklist_item_id,
            order_index=step_data.order_index if step_data.order_index is not None else idx,
            input_mapping=step_data.input_mapping,
            condition=step_data.condition,
            on_failure=step_data.on_failure,
            timeout_override=step_data.timeout_override,
            target_mode=step_data.target_mode,
            target_filter=step_data.target_filter,
            ui_position=step_data.ui_position,
        )
        db.add(step)
    
    await db.flush()
    # Capture ID before expiring
    new_flow_id = flow.id
    
    # Reload with eager fetching to avoid MissingGreenlet in _flow_to_response
    db.expire_all()
    result = await db.execute(
        select(Flow).where(Flow.id == new_flow_id)
        .options(selectinload(Flow.steps).joinedload(FlowStep.checklist_item))
    )
    flow = result.scalar_one()
    try:
        payload = await flow_sync_payload(db, flow)
        await sync_service.record_event(
            db,
            entity_type="flows",
            entity_public_id=flow.public_id,
            operation="create",
            payload=payload,
        )
        for step in flow.steps:
            step_payload = await flow_step_sync_payload(db, step)
            await sync_service.record_event(
                db,
                entity_type="flow_steps",
                entity_public_id=step.public_id,
                operation="create",
                payload=step_payload,
            )
    except Exception as e:
        print(f"[Flows] Sync record (create_flow) failed: {e}")
    return _flow_to_response(flow)


@router.get("/{flow_id}", response_model=FlowResponse)
async def get_flow(flow_id: int, db: AsyncSession = Depends(get_db)):
    """Get a flow by ID."""
    result = await db.execute(
        select(Flow).where(Flow.id == flow_id)
        .options(selectinload(Flow.steps).selectinload(FlowStep.checklist_item))
    )
    flow = result.scalar_one_or_none()
    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")

    ad_domain_name = None
    if getattr(flow, "ad_domain_id", None):
        from app.models.ad_domain import ADDomain
        dr = await db.execute(select(ADDomain.name).where(ADDomain.id == flow.ad_domain_id))
        row = dr.first()
        if row:
            ad_domain_name = row[0]

    return _flow_to_response(flow, ad_domain_name=ad_domain_name)


@router.put("/{flow_id}", response_model=FlowResponse)
async def update_flow(flow_id: int, data: FlowUpdate, db: AsyncSession = Depends(get_db)):
    """Update a flow."""
    result = await db.execute(
        select(Flow).where(Flow.id == flow_id)
        .options(selectinload(Flow.steps))
    )
    flow = result.scalar_one_or_none()
    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")
    
    for field, value in data.model_dump(exclude_unset=True, exclude={"steps"}).items():
        setattr(flow, field, value)
    
    if data.steps is not None:
        # Record step deletes for sync before removing
        for step in flow.steps:
            try:
                step_payload = await flow_step_sync_payload(db, step)
                await sync_service.record_event(
                    db,
                    entity_type="flow_steps",
                    entity_public_id=step.public_id,
                    operation="delete",
                    payload=step_payload,
                )
            except Exception as e:
                print(f"[Flows] Sync record (step delete) failed: {e}")
        # Clear existing steps
        for step in flow.steps:
            await db.delete(step)
        
        # Add new steps
        for idx, step_data in enumerate(data.steps):
            step = FlowStep(
                flow_id=flow.id,
                checklist_item_id=step_data.checklist_item_id,
                # Honor the editor-supplied order_index (topological depth) so
                # same-depth steps stay in one parallel stage and {depth.var}
                # mappings keep resolving to the right stage. Fall back to array
                # position only for callers that don't send it.
                order_index=step_data.order_index if step_data.order_index is not None else idx,
                input_mapping=step_data.input_mapping,
                condition=step_data.condition,
                on_failure=step_data.on_failure,
                timeout_override=step_data.timeout_override,
                target_mode=step_data.target_mode,
                target_filter=step_data.target_filter,
                ui_position=step_data.ui_position,
            )
            db.add(step)
    
    await db.flush()
    # Reload with eager fetching
    db.expire_all()
    result = await db.execute(
        select(Flow).where(Flow.id == flow_id)
        .options(selectinload(Flow.steps).joinedload(FlowStep.checklist_item))
    )
    flow = result.scalar_one()
    try:
        payload = await flow_sync_payload(db, flow)
        await sync_service.record_event(
            db,
            entity_type="flows",
            entity_public_id=flow.public_id,
            operation="update",
            payload=payload,
        )
        if data.steps is not None:
            for step in flow.steps:
                step_payload = await flow_step_sync_payload(db, step)
                await sync_service.record_event(
                    db,
                    entity_type="flow_steps",
                    entity_public_id=step.public_id,
                    operation="create",
                    payload=step_payload,
                )
    except Exception as e:
        print(f"[Flows] Sync record (update_flow) failed: {e}")
    return _flow_to_response(flow)


@router.delete("/{flow_id}", status_code=204)
async def delete_flow(flow_id: int, db: AsyncSession = Depends(get_db)):
    """Delete a flow."""
    result = await db.execute(
        select(Flow).where(Flow.id == flow_id).options(selectinload(Flow.steps))
    )
    flow = result.scalar_one_or_none()
    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")
    try:
        payload = await flow_sync_payload(db, flow)
        await sync_service.record_event(
            db,
            entity_type="flows",
            entity_public_id=flow.public_id,
            operation="delete",
            payload=payload,
        )
    except Exception as e:
        print(f"[Flows] Sync record (delete_flow) failed: {e}")
    await db.delete(flow)


@router.post("/import", status_code=201)
async def import_flow(
    project_id: int,
    template_flow_ids: List[int],
    db: AsyncSession = Depends(get_db),
    ad_domain_id: Optional[int] = None,
):
    """Import flow templates into a project."""
    result = await db.execute(
        select(Flow)
        .where(Flow.id.in_(template_flow_ids))
        .options(selectinload(Flow.steps).selectinload(FlowStep.checklist_item))
    )
    templates = result.scalars().all()

    # Build domain-scoped item lookup when ad_domain_id provided
    domain_item_map = {}
    if ad_domain_id and project_id:
        domain_items_result = await db.execute(
            select(ChecklistItem)
            .join(ChecklistGroup)
            .where(
                ChecklistGroup.project_id == project_id,
                ChecklistGroup.ad_domain_id == ad_domain_id,
            )
        )
        for item in domain_items_result.scalars().all():
            domain_item_map[item.name] = item.id
    
    imported = []
    warnings = []
    for tmpl in templates:
        new_flow = Flow(
            project_id=project_id,
            name=tmpl.name,
            description=tmpl.description,
            is_template=False,
            tags=tmpl.tags,
            flow_definition=tmpl.flow_definition,
            ad_domain_id=ad_domain_id,
        )
        db.add(new_flow)
        await db.flush()
        
        for step in tmpl.steps:
            resolved_item_id = step.checklist_item_id
            if ad_domain_id and step.checklist_item:
                domain_match = domain_item_map.get(step.checklist_item.name)
                if domain_match:
                    resolved_item_id = domain_match
                else:
                    warnings.append(
                        f"Flow '{tmpl.name}': Item '{step.checklist_item.name}' "
                        f"not found in domain scope, using original."
                    )

            new_step = FlowStep(
                flow_id=new_flow.id,
                checklist_item_id=resolved_item_id,
                order_index=step.order_index,
                input_mapping=step.input_mapping,
                condition=step.condition,
                on_failure=step.on_failure,
                timeout_override=step.timeout_override,
                target_mode=getattr(step, "target_mode", "inherit") or "inherit",
                target_filter=getattr(step, "target_filter", {}) or {},
                ui_position=step.ui_position,
            )
            db.add(new_step)
        await db.flush()
        await db.refresh(new_flow, ["steps"])
        try:
            payload = await flow_sync_payload(db, new_flow)
            await sync_service.record_event(
                db,
                entity_type="flows",
                entity_public_id=new_flow.public_id,
                operation="create",
                payload=payload,
            )
            for new_step in new_flow.steps:
                step_payload = await flow_step_sync_payload(db, new_step)
                await sync_service.record_event(
                    db,
                    entity_type="flow_steps",
                    entity_public_id=new_step.public_id,
                    operation="create",
                    payload=step_payload,
                )
        except Exception as e:
            print(f"[Flows] Sync record (import_flow) failed: {e}")
        imported.append(new_flow)
        
    await db.commit()
    resp = {"status": "imported", "count": len(imported)}
    if warnings:
        resp["warnings"] = warnings
    return resp


@router.post("/{flow_id}/execute", response_model=FlowExecutionResponse)
async def execute_flow(flow_id: int, data: FlowExecutionCreate, db: AsyncSession = Depends(get_db)):
    """Execute a flow (starts initial steps in background)."""
    from app.core.flow_manager import flow_manager

    try:
        flow_exec, _executions = await flow_manager.start_flow(
            db=db,
            flow_id=flow_id,
            variables=data.variables,
            host_id=data.host_id,
            target=data.target,
            targets=data.targets or [],
            host_ids=data.host_ids or [],
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return FlowExecutionResponse(
        id=flow_exec.id,
        flow_id=flow_exec.flow_id,
        status=flow_exec.status,
        variables=flow_exec.variables or {},
        target=flow_exec.target,
        targets=flow_exec.targets or [],
        host_id=flow_exec.host_id,
        started_at=flow_exec.started_at,
        completed_at=flow_exec.completed_at,
        created_at=flow_exec.created_at,
        error=flow_exec.error,
    )


@router.post("/{flow_id}/execution/{flow_execution_id}/stop")
async def stop_flow_execution(
    flow_id: int, 
    flow_execution_id: str, 
    db: AsyncSession = Depends(get_db)
):
    """Stop all running tasks for a flow execution."""
    from app.models.execution import Execution, ExecutionStatus
    from app.core.orchestrator import task_orchestrator
    
    result = await db.execute(select(Flow).where(Flow.id == flow_id))
    if not result.scalar_one_or_none():
         raise HTTPException(status_code=404, detail="Flow not found")

    result = await db.execute(
        select(Execution)
        .where(Execution.flow_execution_id == flow_execution_id)
        .where(Execution.status.in_([ExecutionStatus.RUNNING, ExecutionStatus.PENDING]))
    )
    executions = result.scalars().all()
    
    cancelled_count = 0
    for exc in executions:
        if exc.status == ExecutionStatus.RUNNING:
            if await task_orchestrator.cancel_execution(exc.id):
                cancelled_count += 1
        exc.status = ExecutionStatus.CANCELLED
        exc.stderr = "Flow execution stopped by user."
        exc.completed_at = datetime.utcnow()

    # Update FlowExecution record
    fe_result = await db.execute(
        select(FlowExecution).where(FlowExecution.id == flow_execution_id)
    )
    flow_exec = fe_result.scalar_one_or_none()
    if flow_exec and flow_exec.status in ("pending", "running"):
        flow_exec.status = "cancelled"
        flow_exec.completed_at = datetime.utcnow()

    await db.commit()
    
    return {"status": "stopped", "cancelled_count": cancelled_count}


@router.post("/{flow_id}/execution/{flow_execution_id}/pause")
async def pause_flow_execution(
    flow_id: int,
    flow_execution_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Pause a running flow execution. Cancels the currently running step immediately."""
    from app.models.execution import Execution, ExecutionStatus
    from app.core.orchestrator import task_orchestrator

    fe_result = await db.execute(
        select(FlowExecution).where(FlowExecution.id == flow_execution_id)
    )
    flow_exec = fe_result.scalar_one_or_none()
    if not flow_exec:
        raise HTTPException(status_code=404, detail="Flow execution not found")
    if flow_exec.status != "running":
        raise HTTPException(status_code=400, detail=f"Cannot pause flow in '{flow_exec.status}' state")

    result = await db.execute(
        select(Execution)
        .where(Execution.flow_execution_id == flow_execution_id)
        .where(Execution.status.in_([ExecutionStatus.RUNNING, ExecutionStatus.PENDING]))
        .order_by(Execution.flow_step_index.desc())
    )
    active_executions = result.scalars().all()

    paused_step = None
    for exc in active_executions:
        if paused_step is None and exc.flow_step_index is not None:
            paused_step = exc.flow_step_index
        if exc.status == ExecutionStatus.RUNNING:
            await task_orchestrator.cancel_execution(exc.id)
        exc.status = ExecutionStatus.CANCELLED
        exc.stderr = (exc.stderr or "") + "\n[Paused] Flow execution paused by user."
        exc.completed_at = datetime.utcnow()

    flow_exec.status = "paused"
    flow_exec.paused_at_step = paused_step
    await db.commit()

    return {"status": "paused", "paused_at_step": paused_step}


@router.post("/{flow_id}/execution/{flow_execution_id}/resume")
async def resume_flow_execution(
    flow_id: int,
    flow_execution_id: str,
    db: AsyncSession = Depends(get_db),
):
    """Resume a paused flow execution from the step it was paused at."""
    from app.core.flow_manager import flow_manager
    from app.models.execution import Execution, ExecutionStatus

    fe_result = await db.execute(
        select(FlowExecution).where(FlowExecution.id == flow_execution_id)
    )
    flow_exec = fe_result.scalar_one_or_none()
    if not flow_exec:
        raise HTTPException(status_code=404, detail="Flow execution not found")
    if flow_exec.status != "paused":
        raise HTTPException(status_code=400, detail=f"Cannot resume flow in '{flow_exec.status}' state")

    paused_step = flow_exec.paused_at_step
    if paused_step is None:
        raise HTTPException(status_code=400, detail="No paused step recorded")

    flow_result = await db.execute(
        select(Flow).where(Flow.id == flow_id)
        .options(selectinload(Flow.steps).selectinload(FlowStep.checklist_item))
    )
    flow = flow_result.scalar_one_or_none()
    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")

    steps_to_run = [s for s in flow.steps if s.order_index == paused_step]
    if not steps_to_run:
        raise HTTPException(status_code=400, detail="Paused step no longer exists in flow")

    flow_exec.status = "running"
    flow_exec.paused_at_step = None

    project_vars = await flow_manager._load_project_variables(
        db, flow.project_id,
        ad_domain_id=getattr(flow, "ad_domain_id", None),
        host_id=flow_exec.host_id,
    )

    base_vars = merge_set_values(dict(project_vars), flow_exec.variables)
    flow_manager._inject_targets(base_vars, flow_exec.target, flow_exec.targets or [])
    base_vars["flow_id"] = flow_id

    all_execs_result = await db.execute(
        select(Execution)
        .where(Execution.flow_execution_id == flow_execution_id)
        .options(selectinload(Execution.checklist_item))
    )
    all_execs = all_execs_result.scalars().all()

    execution_outputs: dict = {}
    item_name_to_exe: dict = {}
    for exc in all_execs:
        if exc.parsed_output:
            execution_outputs[f"exec_{exc.id}"] = exc.parsed_output
            execution_outputs[exc.id] = exc.parsed_output
            if exc.flow_step_index is not None:
                execution_outputs[f"step_{exc.flow_step_index}"] = exc.parsed_output
                execution_outputs[exc.flow_step_index] = exc.parsed_output
        if exc.checklist_item:
            item_name_to_exe[exc.checklist_item.name] = exc.id

    import asyncio
    from app.core.orchestrator import task_orchestrator

    for step in steps_to_run:
        step_vars = await flow_manager._resolve_step_variables(
            step, base_vars, execution_outputs, item_name_to_exe
        )
        step_vars["flow_id"] = flow_id
        if flow.project_id:
            step_vars["project_id"] = flow.project_id

        if not flow_manager._evaluate_condition(step, base_vars, execution_outputs, item_name_to_exe):
            continue

        step_host_id, step_target_vars = await flow_manager._resolve_step_targets(
            db, step, flow_exec.host_id, flow_exec.targets or [],
            flow_exec.target, flow.project_id, step_vars,
            ad_domain_id=getattr(flow, "ad_domain_id", None),
        )
        step_vars.update(step_target_vars)

        execution = await task_orchestrator.create_execution_record(
            db=db,
            item_id=step.checklist_item_id,
            host_id=step_host_id,
            variables=step_vars,
            flow_execution_id=flow_execution_id,
            flow_step_index=step.order_index,
        )
        await db.commit()

        asyncio.create_task(
            task_orchestrator.run_execution_background(
                execution.id,
                step.checklist_item_id,
                step_host_id,
                step_vars,
                flow_execution_id,
                step.order_index,
            )
        )

    await db.commit()
    return {"status": "resumed", "resumed_at_step": paused_step}


@router.get("/{flow_id}/status")
async def get_flow_execution_status(
    flow_id: int,
    flow_execution_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Get step-by-step execution status for a flow (latest or specific run)."""
    from app.models.execution import Execution, ExecutionStatus

    result = await db.execute(
        select(Flow).where(Flow.id == flow_id)
        .options(selectinload(Flow.steps).selectinload(FlowStep.checklist_item))
    )
    flow = result.scalar_one_or_none()
    if not flow:
        raise HTTPException(status_code=404, detail="Flow not found")

    # Find the FlowExecution to display
    if flow_execution_id:
        fe_result = await db.execute(
            select(FlowExecution).where(FlowExecution.id == flow_execution_id)
        )
        flow_exec = fe_result.scalar_one_or_none()
    else:
        fe_result = await db.execute(
            select(FlowExecution)
            .where(FlowExecution.flow_id == flow_id)
            .order_by(FlowExecution.created_at.desc())
            .limit(1)
        )
        flow_exec = fe_result.scalar_one_or_none()

    latest_flow_exec_id = flow_exec.id if flow_exec else None
    executions: list = []
    if latest_flow_exec_id:
        exec_result = await db.execute(
            select(Execution)
            .where(Execution.flow_execution_id == latest_flow_exec_id)
        )
        executions = exec_result.scalars().all()

    # Collect batch parent IDs for child count queries
    batch_parent_ids = [
        e.id for e in executions
        if (e.variables_used or {}).get("is_batch_parent")
    ]
    batch_child_counts: dict = {}
    if batch_parent_ids:
        all_children_result = await db.execute(
            select(Execution).where(
                Execution.item_id.in_([e.item_id for e in executions if e.id in batch_parent_ids])
            )
        )
        all_children = all_children_result.scalars().all()
        for pid in batch_parent_ids:
            children = [
                c for c in all_children
                if (c.variables_used or {}).get("parent_execution_id") == pid
            ]
            done = sum(1 for c in children if c.status in (
                ExecutionStatus.COMPLETED, ExecutionStatus.FAILED,
                ExecutionStatus.CANCELLED, ExecutionStatus.TIMEOUT,
            ))
            ok = sum(1 for c in children if c.status == ExecutionStatus.COMPLETED)
            batch_child_counts[pid] = {
                "total": len(children),
                "completed": done,
                "successful": ok,
            }

    step_statuses = []
    for step in sorted(flow.steps, key=lambda s: s.order_index):
        step_exec = next(
            (e for e in executions if e.flow_step_index == step.order_index),
            None,
        )
        is_batch = bool(step_exec and (step_exec.variables_used or {}).get("is_batch_parent"))
        batch_info = batch_child_counts.get(step_exec.id) if step_exec else None
        step_statuses.append({
            "order_index": step.order_index,
            "item_id": step.checklist_item_id,
            "item_name": step.checklist_item.name if step.checklist_item else None,
            "status": step_exec.status.value if step_exec else "pending",
            "execution_id": step_exec.id if step_exec else None,
            "started_at": step_exec.started_at.isoformat() if step_exec and step_exec.started_at else None,
            "completed_at": step_exec.completed_at.isoformat() if step_exec and step_exec.completed_at else None,
            "exit_code": step_exec.exit_code if step_exec else None,
            "is_batch": is_batch,
            "batch_progress": batch_info,
        })

    overall_status = flow_exec.status if flow_exec else "pending"

    return {
        "flow_id": flow_id,
        "flow_name": flow.name,
        "flow_execution_id": latest_flow_exec_id,
        "overall_status": overall_status,
        "target": flow_exec.target if flow_exec else None,
        "targets": flow_exec.targets if flow_exec else [],
        "host_id": flow_exec.host_id if flow_exec else None,
        "steps": step_statuses,
    }


@router.get("/{flow_id}/executions", response_model=List[FlowExecutionResponse])
async def list_flow_executions(flow_id: int, db: AsyncSession = Depends(get_db)):
    """List all execution runs for a flow."""
    result = await db.execute(
        select(FlowExecution)
        .where(FlowExecution.flow_id == flow_id)
        .order_by(FlowExecution.created_at.desc())
        .limit(50)
    )
    return [
        FlowExecutionResponse(
            id=fe.id, flow_id=fe.flow_id, status=fe.status,
            variables=fe.variables or {}, target=fe.target,
            targets=fe.targets or [], host_id=fe.host_id,
            started_at=fe.started_at, completed_at=fe.completed_at,
            created_at=fe.created_at, error=fe.error,
        )
        for fe in result.scalars().all()
    ]


@router.get("/execution/{flow_execution_id}/context")
async def get_execution_context(flow_execution_id: str, db: AsyncSession = Depends(get_db)):
    """Get current variable context for a flow execution."""
    from app.models.execution import Execution
    
    # Fetch all executions for this flow run
    stmt = (
        select(Execution)
        .where(Execution.flow_execution_id == flow_execution_id)
        .options(joinedload(Execution.checklist_item))
    )
    executions = (await db.execute(stmt)).scalars().unique().all()
    
    if not executions:
        raise HTTPException(status_code=404, detail="Flow execution not found")
        
    context = {}
    for exc in executions:
        if exc.parsed_output:
            # Add to context under execution ID
            # context[f"{exc.item_id}.outputs"] = exc.parsed_output # Schema undefined in prompt, using flat map?
            # Prompt says: { "execution_id_123": { ... } }
            
            # We need to map back which "Ref ID" corresponds to which tool
            # But the prompt uses "task_ref_id" (e.g. nmap_scan_1). 
            # Our Template Engine uses Execution ID or Item Name.
            
            # Using Execution ID:
            context[str(exc.id)] = {
                "tool_name": exc.checklist_item.name if exc.checklist_item else "unknown",
                "status": exc.status.value if hasattr(exc.status, 'value') else exc.status,
                "outputs": exc.parsed_output
            }
            
            # Also support Item Name for convenience (latest)
            if exc.checklist_item:
                context[exc.checklist_item.name] = {
                    "execution_id": exc.id,
                    "outputs": exc.parsed_output
                }

    return context


@router.get("/execution/{flow_execution_id}/next")
async def get_next_tasks(
    flow_execution_id: str, 
    flow_id: Optional[int] = None, 
    db: AsyncSession = Depends(get_db)
):
    """
    Get tasks ready to be executed.
    Requires flow_id to resolve the definition, or infers it if possible.
    """
    from app.models.execution import Execution
    from app.core.flow_manager import flow_manager as fm
    
    # Get latest execution to infer flow_id if not provided
    stmt = select(Execution).where(Execution.flow_execution_id == flow_execution_id).order_by(Execution.flow_step_index.desc())
    executions = (await db.execute(stmt)).scalars().all()
    
    if not executions and not flow_id:
        raise HTTPException(status_code=404, detail="Execution not found and no flow_id provided")

    # If we have executions, try to determine status
    # This is complex without a full state machine.
    # For now, return a simplified response based on the last step.
    
    current_step_index = -1
    if executions:
        # Check the status of the last one
        last = executions[0]
        if last.status == "completed":
            current_step_index = last.flow_step_index
        elif last.status == "failed":
            return {"status": "blocked", "reason": "Last step failed", "step_index": last.flow_step_index}
        else:
            return {"status": "running", "step_index": last.flow_step_index}
            
    # Calculate next step
    next_index = current_step_index + 1
    
    return {
        "status": "ready",
        "next_step_index": next_index,
        "ready_tasks": [next_index] # Simplified: sequential
    }

def _flow_to_response(flow: Flow, ad_domain_name: Optional[str] = None) -> FlowResponse:
    steps_loaded = 'steps' not in sa_inspect(flow).unloaded
    steps = flow.steps if steps_loaded else []
    return FlowResponse(
        id=flow.id, project_id=flow.project_id, name=flow.name,
        description=flow.description, is_template=flow.is_template,
        is_default_import=flow.is_default_import, requires_auth=flow.requires_auth,
        tags=flow.tags, flow_definition=flow.flow_definition,
        created_at=flow.created_at, updated_at=flow.updated_at,
        ad_domain_id=getattr(flow, "ad_domain_id", None),
        ad_domain_name=ad_domain_name or getattr(flow, "_ad_domain_name", None),
        steps=[{
            "id": s.id, "flow_id": s.flow_id,
            "checklist_item_id": s.checklist_item_id,
            "order_index": s.order_index,
            "input_mapping": s.input_mapping,
            "condition": s.condition,
            "on_failure": s.on_failure,
            "timeout_override": s.timeout_override,
            "target_mode": getattr(s, "target_mode", "inherit") or "inherit",
            "target_filter": getattr(s, "target_filter", {}) or {},
            "ui_position": s.ui_position,
            "item_name": s.checklist_item.name if s.checklist_item else None,
            "item_command": s.checklist_item.command_template if s.checklist_item else None,
            "output_regex_keys": list((s.checklist_item.output_regex or {}).keys()) if s.checklist_item else [],
            "variables": s.checklist_item.variables if s.checklist_item else {}
        } for s in (steps or [])]
    )


@router.get("/export/json")
async def export_flows_json(
    project_id: Optional[int] = None,
    flow_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db)
):
    """Export flows in JSON format."""
    query = select(Flow).options(
        selectinload(Flow.steps).selectinload(FlowStep.checklist_item)
    )
    
    if flow_id:
        query = query.where(Flow.id == flow_id)
    elif project_id:
        query = query.where(Flow.project_id == project_id)
    else:
        # Export global templates
        query = query.where(Flow.project_id.is_(None))
        
    result = await db.execute(query)
    flows = result.scalars().all()
    
    export_data = {
        "flows": []
    }
    
    for flow in flows:
        flow_data = {
            "name": flow.name,
            "description": flow.description,
            "is_default_import": flow.is_default_import,
            "requires_auth": flow.requires_auth,
            "tags": flow.tags,
            "flow_definition": flow.flow_definition,
            "steps": []
        }
        
        # We export step configuration, but we need to handle checklist_item_id.
        # Ideally, we should export the referenced checklist item or use a portable reference (name).
        # For simplicity in this version, we will warn if items are missing on import, 
        # or we could try to export minimal item info to recreate it?
        # Let's rely on the user having compatible items or updating them.
        # Better approach: Export the item name, and on import try to find it in the project.
        
        for step in flow.steps:
            # We need the item name to make it portable
            item = step.checklist_item
            step_data = {
                "checklist_item_name": item.name if item else None,
                "order_index": step.order_index,
                "input_mapping": step.input_mapping,
                "condition": step.condition,
                "on_failure": step.on_failure,
                "timeout_override": step.timeout_override,
                "target_mode": getattr(step, "target_mode", "inherit") or "inherit",
                "target_filter": getattr(step, "target_filter", {}) or {},
                "ui_position": step.ui_position,
            }
            flow_data["steps"].append(step_data)
            
        export_data["flows"].append(flow_data)
        
    return JSONResponse(
        content=export_data,
        headers={"Content-Disposition": f"attachment; filename=flows_export_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"}
    )


@router.post("/import/file")
async def import_flows_file(
    project_id: Optional[int] = None,
    ad_domain_id: Optional[int] = None,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db)
):
    """Import flows from a JSON file. If project_id is None, import as global templates."""
    if not file.filename.endswith('.json'):
        raise HTTPException(status_code=400, detail="Invalid file format. Please upload a JSON file.")
        
    try:
        content = await file.read()
        data = json.loads(content)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON content.")
        
    if "flows" not in data:
        raise HTTPException(status_code=400, detail="Invalid export file format.")
        
    imported_count = 0
    warnings = []
    
    if ad_domain_id and project_id:
        domain_items_result = await db.execute(
            select(ChecklistItem)
            .join(ChecklistGroup)
            .where(
                ChecklistGroup.project_id == project_id,
                ChecklistGroup.ad_domain_id == ad_domain_id,
            )
        )
        domain_items = {item.name: item.id for item in domain_items_result.scalars().all()}
    else:
        domain_items = {}

    if project_id:
        project_items_result = await db.execute(
            select(ChecklistItem)
            .join(ChecklistGroup)
            .where(ChecklistGroup.project_id == project_id)
        )
    else:
        project_items_result = await db.execute(
            select(ChecklistItem)
            .join(ChecklistGroup)
            .where(ChecklistGroup.project_id.is_(None))
        )
        
    project_items = {item.name: item.id for item in project_items_result.scalars().all()}
    
    for flow_data in data["flows"]:
        # Create flow
        new_flow = Flow(
            project_id=project_id,
            name=flow_data["name"],
            description=flow_data.get("description"),
            is_template=True if project_id is None else False,
            is_default_import=flow_data.get("is_default_import", False) if project_id is None else False,
            requires_auth=flow_data.get("requires_auth", False) if project_id is None else False,
            tags=flow_data.get("tags", []),
            flow_definition=flow_data.get("flow_definition", {}),
            ad_domain_id=ad_domain_id if project_id else None,
        )
        db.add(new_flow)
        await db.flush()
        
        steps_added = 0
        for i, step_data in enumerate(flow_data.get("steps", [])):
            item_name = step_data.get("checklist_item_name")
            target_item_id = None
            
            if item_name and ad_domain_id and item_name in domain_items:
                target_item_id = domain_items[item_name]
            elif item_name and item_name in project_items:
                target_item_id = project_items[item_name]
            else:
                if item_name:
                    fallback_result = await db.execute(
                        select(ChecklistItem).where(ChecklistItem.name == item_name).limit(1)
                    )
                    fallback_item = fallback_result.scalar_one_or_none()
                    if fallback_item:
                        target_item_id = fallback_item.id

            if not target_item_id:
                warnings.append(f"Flow '{new_flow.name}': Step {i+1} skipped. Item '{item_name}' not found.")
                continue
                
            new_step = FlowStep(
                flow_id=new_flow.id,
                checklist_item_id=target_item_id,
                order_index=step_data.get("order_index", i),
                input_mapping=step_data.get("input_mapping") or {},
                condition=step_data.get("condition"),
                on_failure=step_data.get("on_failure", "stop"),
                timeout_override=step_data.get("timeout_override"),
                target_mode=step_data.get("target_mode", "inherit"),
                target_filter=step_data.get("target_filter", {}),
                ui_position=step_data.get("ui_position", {}),
            )
            db.add(new_step)
            steps_added += 1

        if steps_added > 0:
            imported_count += 1
        else:
            warnings.append(f"Flow '{new_flow.name}': No steps could be imported.")
        
    await db.commit()
    
    response_data = {"status": "imported", "count": imported_count}
    if warnings:
        response_data["warnings"] = warnings
        
    return response_data
