"""Variable injection upsert (extracted from TaskOrchestrator).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.notifications import notification_manager
from app.core.sync import project_variable_sync_payload, sync_service
from app.models.checklist import ChecklistItem
from app.models.execution import Execution

logger = logging.getLogger(__name__)


async def upsert_variable(
    db: AsyncSession, project_id: int, var_key: str,
    val: Any, target_host_id: Optional[int],
):
    """Insert-or-update one live variable row; returns (row, operation).

    A concurrent insert for the same scope raises IntegrityError at flush:
    reselect the winner and apply last-writer-wins instead of duplicating.
    SQLITE_BUSY (single-writer SQLite vs a concurrent committer) retries
    with backoff — the reselect then sees the committed winner.
    """
    from app.models.variable import ProjectVariable

    async def _reselect():
        stmt = select(ProjectVariable).where(
            ProjectVariable.project_id == project_id,
            ProjectVariable.key == var_key,
            ProjectVariable.host_id == target_host_id,
            ProjectVariable.ad_domain_id.is_(None),
            ProjectVariable.deleted_at.is_(None),
        )
        result = await db.execute(stmt)
        return result.scalars().first()

    last_busy: Optional[Exception] = None
    for _ in range(6):
        try:
            try:
                async with db.begin_nested():
                    row = await _reselect()
                    operation = "update"
                    if row is not None:
                        row.value = val
                    else:
                        row = ProjectVariable(
                            project_id=project_id, key=var_key, value=val,
                            host_id=target_host_id)
                        db.add(row)
                        operation = "create"
                    await db.flush()
            except IntegrityError:
                async with db.begin_nested():
                    row = await _reselect()
                    if row is None:
                        raise
                    row.value = val
                    operation = "update"
                    await db.flush()
            return row, operation
        except OperationalError as e:
            msg = str(e).lower()
            if "locked" not in msg and "busy" not in msg:
                raise
            last_busy = e
            await asyncio.sleep(0.05)
    raise last_busy  # type: ignore[misc]


async def process_variable_injection(db: AsyncSession, execution: Execution, item: ChecklistItem, parsed_results: Dict[str, Any],
    *, var_locks: List[asyncio.Lock],
):
    """Inject parsed outputs into Project Variables based on policy."""
    policy = item.storage_policy or {}
    mapping = policy.get("variable_mapping", {})

    logger.debug(f"[DEBUG] Processing variable injection for item {item.id}. Mapping: {mapping}")

    if not item.group or not item.group.project_id:
        return

    # A8: only inject keys the user EXPLICITLY mapped. Without this, any item with an
    # output_regex auto-created/overwrote project variables from every parsed key,
    # silently clobbering operator-set vars like {target}/{port}.
    if not mapping:
        return

    project_id = item.group.project_id
    from app.models.variable import ProjectVariable

    updates_to_notify = []

    # Prepare context for keys (e.g. {host_id}, {target})
    # This allows mapping: "open_ports" -> "open_ports_{host_id}" if user really wants dynamic keys
    # BUT our main goal is using the host_id COLUMN.

    for output_key, result_obj in parsed_results.items():
        if output_key not in mapping:
            continue
        var_key = mapping[output_key]

        # Render key names if they contain placeholders (e.g. "ports_{target}")
        # We use a simple Replace because we don't have full context here easily without huge overhead
        # But let's support basic {host_id}
        if "{host_id}" in var_key and execution.host_id:
            var_key = var_key.replace("{host_id}", str(execution.host_id))

        val = result_obj.value

        # Logic: 
        # 1. ALWAYS valid global/project-wide variable (host_id=None)
        # 2. IF execution.host_id is present, ALSO save as Host-Specific Variable (host_id=execution.host_id)
        #    Wait, do we save BOTH? 
        #    User request: "save output of that checklist item... make it target specific output"
        #    If we save ONLY host-specific, then "ports" becomes empty globally?
        #    If we save BOTH, which one takes precedence? 
        #    The implementation in `execute_item` will prefer Host Specific.
        #    So we should save to Host Specific IF we have a host.

        target_host_id = execution.host_id

        # Check if we should force global (optional policy, but let's default to scoped if possible)
        # If user wants global, they can't easily specify "start with global" unless we add a flag.
        # For now: If running against a Host, save to that Host.

        # Stripe-serialized per scope+key (single-process airtight); the
        # savepoint + IntegrityError path below stays as defense-in-depth.
        stripe = var_locks[
            hash((project_id, target_host_id, var_key)) % len(var_locks)]
        async with stripe:
            row, operation = await upsert_variable(
                db, project_id, var_key, val, target_host_id)

        await db.refresh(row)
        try:
            payload = await project_variable_sync_payload(db, row)
            await sync_service.record_event(
                db,
                entity_type="project_variables",
                entity_public_id=row.public_id,
                operation=operation,
                payload=payload,
            )
        except Exception as e:
            logger.error(f"[Orchestrator] Sync record (project_variable {operation}) failed: {e}")

        updates_to_notify.append(var_key)

    if updates_to_notify:
        await db.flush()

        for vk in updates_to_notify:
             await notification_manager.send_project_update(
                 "variable_updated",
                 project_id,
                 {"key": vk, "host_id": execution.host_id}
             )
