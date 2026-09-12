"""Task orchestrator for async subprocess execution."""
import asyncio
import json
import os
import signal
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text, inspect as sa_inspect
from sqlalchemy.orm import selectinload

from app.config import settings
from app.models.execution import Execution, ExecutionOutput, ExecutionStatus
from app.models.checklist import ChecklistItem, ChecklistGroup
from app.models.flow import Flow
from app.core.parser import output_parser
from app.core.templating import template_engine
from app.core.notifications import notification_manager
from app.core.host_runner import host_runner
from app.core.utils import is_unset, as_target_list, excluded_targets
from app.core.sync import (
    sync_service,
    execution_sync_payload,
    host_sync_payload,
    service_sync_payload,
    discovered_file_sync_payload,
    project_variable_sync_payload,
)
from app.models.file import DiscoveredFile
from app.core.service_parser import service_parser
from app.core.service_parser import ServiceInfo
from app.models.host import Host, Service

# Service names that are generic placeholders; do not overwrite a specific name with these
GENERIC_SERVICE_NAMES = frozenset({
    "unknown", "tcpwrapped", "tcpwrapped|ssl", "ssl|tcpwrapped",
    "tcpwrapped|http", "http|tcpwrapped", "tcpwrapped|https", "https|tcpwrapped",
})


def _is_generic_service_name(name: Optional[str]) -> bool:
    """Return True if the service name is a generic placeholder (e.g. tcpwrapped)."""
    if not name or not name.strip():
        return True
    return name.strip().lower() in GENERIC_SERVICE_NAMES


def _should_update_service_name(existing_name: Optional[str], new_name: Optional[str]) -> bool:
    """Return True if we should overwrite existing name with new name. Prefer specific over generic."""
    if not new_name or not new_name.strip():
        return False
    new_lower = new_name.strip().lower()
    if new_lower == "unknown" or new_lower in GENERIC_SERVICE_NAMES:
        return False  # never overwrite with generic
    if not existing_name or not existing_name.strip():
        return True  # no existing or empty -> accept new
    existing_lower = existing_name.strip().lower()
    if existing_lower in GENERIC_SERVICE_NAMES or existing_lower == "unknown":
        return True  # existing is generic -> allow overwrite with specific
    return new_lower != existing_lower  # both specific: only update if different


def _merge_duplicate_services(services: List[ServiceInfo]) -> List[ServiceInfo]:
    """
    Deduplicate by (port, protocol) and merge: keep best name/version/product, merge script_output.
    Avoids last-one-wins when the same port appears multiple times in parsed output.
    """
    by_key: Dict[tuple, List[ServiceInfo]] = {}
    for svc in services:
        key = (svc.port, svc.protocol)
        by_key.setdefault(key, []).append(svc)
    result: List[ServiceInfo] = []
    for (port, protocol), group in by_key.items():
        if len(group) == 1:
            result.append(group[0])
            continue
        merged_scripts: Dict[str, str] = {}
        for s in group:
            merged_scripts.update(getattr(s, "script_output", None) or {})
        best_name: Optional[str] = None
        best_product: Optional[str] = None
        best_version: Optional[str] = None
        best_state: str = "open"
        for s in group:
            if s.state and s.state != "open":
                best_state = s.state
            if s.name and not _is_generic_service_name(s.name):
                best_name = s.name
            if not best_name and s.name:
                best_name = s.name
            if s.product:
                best_product = s.product
            if s.version:
                best_version = s.version
        merged = ServiceInfo(
            port=port,
            protocol=protocol,
            state=best_state,
            name=best_name or group[0].name,
            product=best_product or group[0].product,
            version=best_version or group[0].version,
            extra_info=group[0].extra_info,
            script_output=merged_scripts,
        )
        result.append(merged)
    return result


# Map service names (from Nmap/tool output) to canonical tags for host tagging
SERVICE_TAG_MAP = {
    "http": "http",
    "https": "http",
    "ssl": "ssl",
    "ssh": "ssh",
    "smb": "smb",
    "microsoft-ds": "smb",
    "netbios-ssn": "smb",
    "ftp": "ftp",
    "smtp": "smtp",
    "mysql": "mysql",
    "mssql": "mssql",
    "ms-sql-s": "mssql",
    "rdp": "rdp",
    "ms-wbt-server": "rdp",
    "dns": "dns",
    "domain": "dns",
    "snmp": "snmp",
    "ldap": "ldap",
    "nfs": "nfs",
    "vnc": "vnc",
    "telnet": "telnet",
    "pop3": "pop3",
    "imap": "imap",
    "kerberos": "kerberos",
    "msrpc": "msrpc",
    "ajp13": "ajp",
}


@dataclass
class TaskContext:
    """Context for a running task."""
    execution_id: int
    process: Optional[asyncio.subprocess.Process] = None
    stdout_buffer: str = ""
    stderr_buffer: str = ""
    cancelled: bool = False
    started_at: Optional[datetime] = None


class TaskOrchestrator:
    """Async orchestrator for CLI tool execution."""
    
    def __init__(self, max_concurrent: int = None):
        self.max_concurrent = max_concurrent or settings.MAX_CONCURRENT_TASKS
        self._semaphore = asyncio.Semaphore(self.max_concurrent)
        self._running_tasks: Dict[int, TaskContext] = {}
        self._lock = asyncio.Lock()
    
    async def create_execution_record(
        self, db: AsyncSession, item_id: int, host_id: Optional[int] = None,
        variables: Optional[Dict[str, Any]] = None,
        flow_execution_id: Optional[str] = None, flow_step_index: Optional[int] = None,
        command_override: Optional[str] = None,
        started_by_user_id: Optional[int] = None,
        exclude_execution_id: Optional[int] = None,
    ) -> Execution:
        """Create an execution record and return immediately (for background execution).

        exclude_execution_id: an execution to ignore in the "already active" duplicate
        check (used by batch children so the still-RUNNING parent, which shares the
        item/host scope when project_id is unresolved, doesn't block them).
        """
        variables = variables or {}
        
        result = await db.execute(
            select(ChecklistItem)
            .where(ChecklistItem.id == item_id)
            .options(
                selectinload(ChecklistItem.group),
                selectinload(ChecklistItem.executions),
            )
        )
        item = result.scalar_one_or_none()
        if not item:
            raise ValueError(f"Checklist item {item_id} not found")

        duplicate_query = select(Execution.id).where(
            Execution.item_id == item_id,
            Execution.status.in_([ExecutionStatus.PENDING, ExecutionStatus.RUNNING]),
        )
        if host_id is None:
            duplicate_query = duplicate_query.where(Execution.host_id.is_(None))
        else:
            duplicate_query = duplicate_query.where(Execution.host_id == host_id)
        if exclude_execution_id is not None:
            duplicate_query = duplicate_query.where(Execution.id != exclude_execution_id)
        duplicate_result = await db.execute(duplicate_query.limit(1))
        existing_id = duplicate_result.scalar_one_or_none()
        if existing_id:
            raise ValueError(f"Execution already active for item/host scope: {existing_id}")
        
        version = item.next_version
        
        # Create execution with PENDING status
        execution = Execution(
            item_id=item_id, host_id=host_id, version=version,
            status=ExecutionStatus.PENDING, command=command_override or "(pending)",
            variables_used=variables,
            flow_execution_id=flow_execution_id,
            flow_step_index=flow_step_index,
            started_by_user_id=started_by_user_id,
        )
        db.add(execution)
        await db.flush()
        
        # Send initial status notification
        await notification_manager.send_execution_status(
            execution.id, 
            "pending", 
            details={"item_name": item.name},
            item_id=item_id
        )
        
        return execution
    
    async def _fail_execution(self, execution_id: int, item_id: int, message: str) -> None:
        """Mark a pre-created execution FAILED without ever launching a command."""
        from app.database import async_session_maker

        print(f"[Orchestrator] Execution {execution_id} refused: {message}")
        try:
            async with async_session_maker() as db:
                result = await db.execute(
                    select(Execution).where(Execution.id == execution_id)
                )
                execution = result.scalar_one_or_none()
                if execution:
                    execution.status = ExecutionStatus.FAILED
                    execution.stderr = message
                    execution.completed_at = datetime.utcnow()
                    await db.commit()
            await notification_manager.send_execution_status(
                execution_id, "failed", details={"error": message}, item_id=item_id
            )
        except Exception as e:
            print(f"[Orchestrator] Failed to record refusal for {execution_id}: {e}")

    async def run_execution_background(
        self, execution_id: int, item_id: int, host_id: Optional[int] = None,
        variables: Optional[Dict[str, Any]] = None,
        flow_execution_id: Optional[str] = None, flow_step_index: Optional[int] = None,
        command_override: Optional[str] = None
    ):
        """Run execution in background with its own database session."""
        from app.database import async_session_maker
        
        # Drop keys whose value carries nothing ("" / [] / {} / None). The UI submits the
        # item's declared variable defaults verbatim, so a declared-but-empty key would
        # otherwise satisfy every ``"x" in variables`` guard below and suppress target
        # resolution, batching and output injection.
        variables = {k: v for k, v in (variables or {}).items() if not is_unset(v)}

        # Resolve the item once: its project scopes the out-of-scope host set, and its
        # command template decides file-based vs iterative batching further down.
        project_id = None
        command_template = None
        async with async_session_maker() as db:
            result = await db.execute(
                select(ChecklistItem)
                .where(ChecklistItem.id == item_id)
                .options(selectinload(ChecklistItem.group))
            )
            item = result.scalar_one_or_none()
            if item:
                command_template = item.command_template
                if item.group:
                    project_id = item.group.project_id
            if project_id is None and variables.get("flow_id"):
                try:
                    flow_result = await db.execute(
                        select(Flow).where(Flow.id == int(variables["flow_id"]))
                    )
                    flow = flow_result.scalar_one_or_none()
                    if flow:
                        project_id = flow.project_id
                except Exception as e:
                    print(f"[Orchestrator] Failed to resolve project_id from flow: {e}")
            excluded = await excluded_targets(db, project_id)
            host_is_excluded = False
            if host_id:
                host_row = (
                    await db.execute(select(Host.excluded).where(Host.id == host_id))
                ).first()
                host_is_excluded = bool(host_row and host_row[0])

        # A host-scoped run (Execute Task on one host, a flow step in "single" target
        # mode) never populates {target} from the picker, so the list filter below would
        # miss it.
        if host_is_excluded:
            await self._fail_execution(
                execution_id, item_id,
                "This host is excluded from scope. "
                "Re-include it in the Assets tab to run against it.",
            )
            return

        # ROE carve-out, enforced here — before the batch/file-based split — so iterative
        # children, the {targets} file and a plain single run are all covered by one
        # guard. Every other entry point funnels through this method.
        if excluded:
            for key in ("target", "targets"):
                if is_unset(variables.get(key)) or key not in variables:
                    continue
                original = variables[key]
                before = as_target_list(original)
                kept = [v for v in before if str(v) not in excluded]
                if len(kept) == len(before):
                    continue
                print(
                    f"[Orchestrator] Dropped {len(before) - len(kept)} out-of-scope "
                    f"target(s) from '{key}' (item {item_id})"
                )
                if not kept:
                    await self._fail_execution(
                        execution_id, item_id,
                        "All requested targets are excluded from scope. "
                        "Re-include them in the Assets tab to run this task.",
                    )
                    return
                variables[key] = (
                    " ".join(str(v) for v in kept) if isinstance(original, str) else kept
                )

        # Check for multiple targets in "target" variable
        raw_target = variables.get("target")
        targets_list = []
        
        if raw_target:
            if isinstance(raw_target, list):
                targets_list = raw_target
            elif isinstance(raw_target, str):
                # Split by space, complying with existing frontend behavior
                targets_list = [t.strip() for t in raw_target.split() if t.strip()]
        
        # Determine if we should use File-Based Batching (Single Execution) vs Iterative Batching
        # Rule: If "targets" (plural) is used in command template OR variables, force File-Based mode.
        use_file_based_batch = False
        
        if "targets" in variables:
            use_file_based_batch = True
        elif command_override:
            # If command_override is provided, we check that instead of item.command_template
            use_file_based_batch = "{targets}" in command_override
        else:
            use_file_based_batch = bool(command_template) and "{targets}" in command_template
        
        if use_file_based_batch:
            # File-Based Batching: Flatten "target" (list) into "targets" (list) if needed
            if targets_list and "targets" not in variables:
                variables["targets"] = targets_list
            
            # SUPPRESS Iterative Batching by clearing the trigger list
            targets_list = []
        
        # If we have multiple targets, run in Iterative Mode (Batch)
        if len(targets_list) > 1:
            await self._run_batch_execution(
                execution_id=execution_id,
                targets=targets_list,
                item_id=item_id,
                base_variables=variables,
                flow_execution_id=flow_execution_id,
                flow_step_index=flow_step_index,
                command_override=command_override
            )
            return

        async with async_session_maker() as db:
            try:
                await self.execute_item(
                    db=db,
                    item_id=item_id,
                    host_id=host_id,
                    variables=variables,
                    flow_execution_id=flow_execution_id,
                    flow_step_index=flow_step_index,
                    execution_id_override=execution_id,  # Use the pre-created execution
                    commit_transaction=True,  # Commit after status=RUNNING to release lock
                    command_override=command_override
                )
                await db.commit()
            except Exception as e:
                await db.rollback()
                # Update execution status to failed
                try:
                    result = await db.execute(
                        select(Execution).where(Execution.id == execution_id)
                    )
                    execution = result.scalar_one_or_none()
                    if execution:
                        execution.status = ExecutionStatus.FAILED
                        execution.stderr = f"Background execution error: {str(e)}"
                        execution.completed_at = datetime.utcnow()
                        await db.commit()
                        await notification_manager.send_execution_status(
                            execution_id, "failed", 
                            details={"error": str(e)},
                            item_id=item_id
                        )
                except Exception:
                    pass  # Last resort - execution record may be lost

    async def _run_batch_execution(
        self, execution_id: int, targets: List[str], item_id: int,
        base_variables: Dict[str, Any],
        flow_execution_id: Optional[str] = None, flow_step_index: Optional[int] = None,
        command_override: Optional[str] = None
    ):
        """Handle batch execution for multiple targets.

        Runs up to ``BATCH_CONCURRENCY`` children in parallel, each in its
        own DB session to prevent identity-map bloat and SQLite lock contention.
        """
        from app.database import async_session_maker

        context = TaskContext(execution_id=execution_id)
        async with self._lock:
            self._running_tasks[execution_id] = context

        success_count = 0
        completed_count = 0
        total = len(targets)
        batch_sem = asyncio.Semaphore(settings.BATCH_CONCURRENCY)
        progress_lock = asyncio.Lock()

        # ── 1. Setup Parent Execution ───────────────────────────────────
        try:
            async with async_session_maker() as parent_db:
                result = await parent_db.execute(
                    select(Execution).where(Execution.id == execution_id)
                )
                parent_execution = result.scalar_one_or_none()

                if not parent_execution:
                    async with self._lock:
                        self._running_tasks.pop(execution_id, None)
                    return

                parent_execution.status = ExecutionStatus.RUNNING
                parent_execution.started_at = datetime.utcnow()
                parent_execution.command = (
                    f"Batch Execution: {total} targets "
                    f"(concurrency={settings.BATCH_CONCURRENCY})"
                )
                parent_execution.variables_used = {
                    **base_variables,
                    "is_batch_parent": True,
                    "target_count": total,
                }

                await parent_db.commit()
                await parent_db.refresh(parent_execution)
                try:
                    payload = await execution_sync_payload(parent_db, parent_execution)
                    await sync_service.record_event(
                        parent_db,
                        entity_type="executions",
                        entity_public_id=parent_execution.public_id,
                        operation="update",
                        payload=payload,
                    )
                    await parent_db.commit()
                except Exception as e:
                    print(f"[Orchestrator] Sync record (batch running) failed: {e}")
                await notification_manager.send_execution_status(
                    execution_id,
                    "running",
                    details={
                        "command": parent_execution.command,
                        "is_batch_parent": True,
                        "completed_count": 0,
                        "success_count": 0,
                        "total": total,
                    },
                    item_id=item_id,
                )

            # ── Pre-fetch project context (short-lived session) ─────────
            project_id = None
            async with async_session_maker() as setup_db:
                if item_id:
                    result = await setup_db.execute(
                        select(ChecklistItem)
                        .where(ChecklistItem.id == item_id)
                        .options(selectinload(ChecklistItem.group))
                    )
                    item = result.scalar_one_or_none()
                    if item and item.group:
                        project_id = item.group.project_id

                    if not project_id and base_variables.get("flow_id"):
                        try:
                            f_res = await setup_db.execute(
                                select(Flow).where(
                                    Flow.id == int(base_variables["flow_id"])
                                )
                            )
                            flow = f_res.scalar_one_or_none()
                            if flow:
                                project_id = flow.project_id
                        except Exception as e:
                            print(f"[Batch] Failed to resolve project_id from flow: {e}")

            # ── 2. Execute Targets (parallel, per-child sessions) ───────
            from app.models.host import Host

            async def _run_single_child(target: str) -> None:
                """Run one child execution under the batch semaphore."""
                nonlocal success_count, completed_count

                if context.cancelled:
                    return

                async with batch_sem:
                    if context.cancelled:
                        return

                    child_vars = base_variables.copy()
                    child_vars["target"] = target
                    child_vars["parent_execution_id"] = execution_id

                    child_succeeded = False
                    try:
                        async with async_session_maker() as child_db:
                            target_host_id = None
                            if project_id:
                                stmt = select(Host).where(
                                    Host.project_id == project_id,
                                    Host.ip_address == target,
                                )
                                result = await child_db.execute(stmt)
                                existing_host = result.scalar_one_or_none()

                                if existing_host:
                                    target_host_id = existing_host.id
                                else:
                                    print(f"[Batch] Auto-creating host for target {target}")
                                    new_host = Host(
                                        project_id=project_id,
                                        ip_address=target,
                                        status="unknown",
                                        extra_data={"discovery_method": "batch_execution_target"},
                                    )
                                    child_db.add(new_host)
                                    await child_db.flush()
                                    await child_db.refresh(new_host)
                                    target_host_id = new_host.id
                                    try:
                                        payload = await host_sync_payload(child_db, new_host)
                                        await sync_service.record_event(
                                            child_db,
                                            entity_type="hosts",
                                            entity_public_id=new_host.public_id,
                                            operation="create",
                                            payload=payload,
                                        )
                                    except Exception as e:
                                        print(f"[Orchestrator] Sync record (batch host) failed: {e}")

                                    await notification_manager.send_project_update(
                                        "host_created",
                                        project_id,
                                        {"ip_address": target, "id": target_host_id},
                                    )

                            child_exec = await self.create_execution_record(
                                db=child_db,
                                item_id=item_id,
                                host_id=target_host_id,
                                variables=child_vars,
                                flow_execution_id=flow_execution_id,
                                flow_step_index=flow_step_index,
                                command_override=command_override,
                                exclude_execution_id=execution_id,  # ignore the running batch parent
                            )

                            completed_exec = await self.execute_item(
                                db=child_db,
                                item_id=item_id,
                                host_id=target_host_id,
                                variables=child_vars,
                                execution_id_override=child_exec.id,
                                commit_transaction=True,
                                command_override=command_override,
                            )

                            if completed_exec.status == ExecutionStatus.COMPLETED:
                                child_succeeded = True

                    except Exception as e:
                        print(f"[Batch] Child execution failed for {target}: {e}")

                    async with progress_lock:
                        if child_succeeded:
                            success_count += 1
                        completed_count += 1
                        current_completed = completed_count
                        current_success = success_count

                    await notification_manager.send_execution_status(
                        execution_id,
                        "running",
                        details={
                            "progress": f"{current_completed}/{total}",
                            "is_batch_parent": True,
                            "completed_count": current_completed,
                            "success_count": current_success,
                            "total": total,
                            "current_target": target,
                        },
                        item_id=item_id,
                    )

            tasks = [
                asyncio.create_task(_run_single_child(t))
                for t in targets
            ]
            await asyncio.gather(*tasks, return_exceptions=True)

            # ── 3. Finalize Parent ──────────────────────────────────────
            async with async_session_maker() as final_db:
                result = await final_db.execute(
                    select(Execution).where(Execution.id == execution_id)
                )
                parent_execution = result.scalar_one_or_none()

                if parent_execution:
                    parent_execution.completed_at = datetime.utcnow()

                    if context.cancelled:
                        parent_execution.status = ExecutionStatus.CANCELLED
                        parent_execution.stderr = "Batch execution cancelled by user."
                        parent_execution.stdout = (
                            f"Cancelled after {completed_count}/{total} targets. "
                            f"{success_count} successful."
                        )
                    elif success_count == total:
                        parent_execution.status = ExecutionStatus.COMPLETED
                        parent_execution.stdout = (
                            f"Batch execution completed. "
                            f"{success_count}/{total} successful."
                        )
                    else:
                        parent_execution.status = ExecutionStatus.FAILED
                        parent_execution.stdout = (
                            f"Batch execution finished. "
                            f"{success_count}/{total} successful, "
                            f"{completed_count - success_count} failed."
                        )

                    # is_success requires exit_code == 0; set it so a fully-successful
                    # batch parent doesn't report is_success=false (None == 0 is False).
                    parent_execution.exit_code = 0 if parent_execution.status == ExecutionStatus.COMPLETED else 1

                    # Aggregate children parsed_output into parent
                    try:
                        children_result = await final_db.execute(
                            select(Execution).where(
                                Execution.item_id == parent_execution.item_id,
                                Execution.status.in_([
                                    ExecutionStatus.COMPLETED,
                                    ExecutionStatus.FAILED,
                                ]),
                                Execution.id != execution_id,
                            )
                        )
                        children = children_result.scalars().all()
                        batch_children = [
                            c for c in children
                            if (c.variables_used or {}).get("parent_execution_id") == execution_id
                        ]
                        aggregated: dict = {}
                        for child in batch_children:
                            for key, value in (child.parsed_output or {}).items():
                                # child values are {"value":…,"data_type":…} wrappers;
                                # collect the inner value so the parent's output is a
                                # usable list, not a list of wrapper dicts.
                                inner = value.get("value") if isinstance(value, dict) else value
                                aggregated.setdefault(key, []).append(inner)
                        if aggregated:
                            parent_execution.parsed_output = {
                                k: {"value": v, "data_type": "list"}
                                for k, v in aggregated.items()
                            }
                    except Exception as agg_err:
                        print(f"[Batch] parsed_output aggregation failed: {agg_err}")

                    await final_db.commit()
                    await final_db.refresh(parent_execution)
                    try:
                        payload = await execution_sync_payload(final_db, parent_execution)
                        await sync_service.record_event(
                            final_db,
                            entity_type="executions",
                            entity_public_id=parent_execution.public_id,
                            operation="update",
                            payload=payload,
                        )
                        await final_db.commit()
                    except Exception as e:
                        print(f"[Orchestrator] Sync record (batch finalize) failed: {e}")

                    await notification_manager.send_execution_status(
                        execution_id,
                        parent_execution.status.value,
                        details={
                            "exit_code": 0 if parent_execution.status == ExecutionStatus.COMPLETED else 1,
                            "is_batch_parent": True,
                            "completed_count": completed_count,
                            "success_count": success_count,
                            "total": total,
                        },
                        item_id=item_id,
                    )

        except Exception as e:
            print(f"[Batch] Fatal batch execution error: {e}")
            try:
                async with async_session_maker() as err_db:
                    result = await err_db.execute(
                        select(Execution).where(Execution.id == execution_id)
                    )
                    parent_execution = result.scalar_one_or_none()
                    if parent_execution and parent_execution.status == ExecutionStatus.RUNNING:
                        parent_execution.status = ExecutionStatus.FAILED
                        parent_execution.completed_at = datetime.utcnow()
                        parent_execution.stderr = f"Batch execution aborted: {str(e)}"
                        parent_execution.stdout = (
                            f"Aborted after {completed_count}/{total} targets. "
                            f"{success_count} successful."
                        )
                        await err_db.commit()
                        await err_db.refresh(parent_execution)
                        try:
                            payload = await execution_sync_payload(err_db, parent_execution)
                            await sync_service.record_event(
                                err_db,
                                entity_type="executions",
                                entity_public_id=parent_execution.public_id,
                                operation="update",
                                payload=payload,
                            )
                            await err_db.commit()
                        except Exception as sync_err:
                            print(f"[Orchestrator] Sync record (batch error) failed: {sync_err}")

                    await notification_manager.send_execution_status(
                        execution_id,
                        "failed",
                        details={
                            "error": str(e),
                            "is_batch_parent": True,
                            "completed_count": completed_count,
                            "success_count": success_count,
                            "total": total,
                        },
                        item_id=item_id,
                    )
            except Exception as inner_e:
                print(f"[Batch] Failed to finalize parent after error: {inner_e}")

        finally:
            async with self._lock:
                self._running_tasks.pop(execution_id, None)
    
    async def execute_item(
        self, db: AsyncSession, item_id: int, host_id: Optional[int] = None,
        variables: Optional[Dict[str, Any]] = None,
        execution_outputs: Optional[Dict[int, Dict[str, Any]]] = None,
        flow_execution_id: Optional[str] = None, flow_step_index: Optional[int] = None,
        output_callback: Optional[Callable[[str], None]] = None,
        execution_id_override: Optional[int] = None,
        commit_transaction: bool = False,
        command_override: Optional[str] = None
    ) -> Execution:
        """Execute a checklist item. If execution_id_override is provided, reuse that execution record."""
        # Defensive copy: callers (esp. execute_bulk) may pass the SAME dict to
        # multiple concurrent execute_item calls; without this, in-place variable
        # injection below bleeds across parallel executions and corrupts variables_used.
        # Empty values are dropped, not copied — see the comment in run_execution_background.
        variables = {k: v for k, v in (variables or {}).items() if not is_unset(v)}
        execution_outputs = execution_outputs or {}
        item_name_to_execution: Dict[str, int] = {}
        
        result = await db.execute(
            select(ChecklistItem)
            .where(ChecklistItem.id == item_id)
            .options(
                selectinload(ChecklistItem.group).selectinload(ChecklistGroup.project)
            )
        )
        item = result.scalar_one_or_none()
        if not item:
            raise ValueError(f"Checklist item {item_id} not found")
        
        # Load Project Context if applicable
        project_id = item.group.project_id if item.group else None
        
        # Inject Project Variables (as defaults, frontend-provided variables take precedence)
        # Resolution order: Global → Domain-scoped → Host-scoped → Frontend overrides
        if project_id:
            from app.models.variable import ProjectVariable
            # 1. Global Variables (host_id=NULL, ad_domain_id=NULL)
            global_vars_stmt = select(ProjectVariable).where(
                ProjectVariable.project_id == project_id,
                ProjectVariable.host_id.is_(None),
                ProjectVariable.ad_domain_id.is_(None),
                ProjectVariable.deleted_at.is_(None),
            )
            global_vars = (await db.execute(global_vars_stmt)).scalars().all()

            # 2. Domain-scoped Variables
            # Priority: checklist group's ad_domain_id → host's extra_data.domain match
            domain_vars = []
            resolved_ad_domain_id = getattr(item.group, "ad_domain_id", None)

            if not resolved_ad_domain_id and host_id:
                from app.models.host import Host as HostModel
                host_row = await db.execute(select(HostModel).where(HostModel.id == host_id))
                host_obj = host_row.scalar_one_or_none()
                host_domain = (host_obj.extra_data or {}).get("domain") if host_obj else None
                if host_domain:
                    from app.models.ad_domain import ADDomain
                    ad_result = await db.execute(
                        select(ADDomain.id).where(
                            ADDomain.project_id == project_id,
                            ADDomain.name == host_domain,
                            ADDomain.deleted_at.is_(None),
                        )
                    )
                    ad_domain_row = ad_result.first()
                    if ad_domain_row:
                        resolved_ad_domain_id = ad_domain_row[0]

            if resolved_ad_domain_id:
                domain_vars_stmt = select(ProjectVariable).where(
                    ProjectVariable.project_id == project_id,
                    ProjectVariable.ad_domain_id == resolved_ad_domain_id,
                    ProjectVariable.deleted_at.is_(None),
                )
                domain_vars = (await db.execute(domain_vars_stmt)).scalars().all()

            # 3. Host-Specific Variables
            host_vars = []
            if host_id:
                host_vars_stmt = select(ProjectVariable).where(
                    ProjectVariable.project_id == project_id,
                    ProjectVariable.host_id == host_id,
                    ProjectVariable.deleted_at.is_(None),
                )
                host_vars = (await db.execute(host_vars_stmt)).scalars().all()

            # 4. Merge: Global → Domain → Host (each layer overrides previous)
            merged_project_vars = {}
            for var in global_vars:
                merged_project_vars[var.key] = var.value
            for var in domain_vars:
                merged_project_vars[var.key] = var.value
            for var in host_vars:
                merged_project_vars[var.key] = var.value

            # Only fill in values that carry content: the seeded library ships project
            # variables like ``targets = ""``, and writing those back would re-create the
            # empty key this method just dropped (killing tag resolution below).
            for key, val in merged_project_vars.items():
                if is_unset(variables.get(key)) and not is_unset(val):
                    variables[key] = val
                
            # Fetch execution outputs from this project
            # Optimization: Only fetching latest valid outputs or all?
            # User wants "alivehosts" from one item to be accessed by another.
            # We can name-space them? "{alivehosts}" might be ambiguous if multiple tasks output it.
            # Usually: {checklist_name.output_key} or just {output_key} (overwriting).
            # Let's assume overwriting or specific namespacing.
            # For "id.variable" mentioned earlier, we need map.
            
            # Query outputs linked to this project
            # Execution -> ChecklistItem -> ChecklistGroup -> Project
            # FIX: Only load outputs relevant to CURRENT HOST (or Global)
            # Loading ALL outputs from ALL hosts creates race conditions and overwrites (Last Writer Wins)
            from sqlalchemy import or_
            
            out_stmt = (
                select(ExecutionOutput, Execution.id, Execution.item_id, ChecklistItem.name)
                .select_from(ExecutionOutput)
                .join(Execution)
                .join(ChecklistItem)
                .join(ChecklistGroup)
                .where(ChecklistGroup.project_id == project_id)
            )

            if host_id:
                out_stmt = out_stmt.where(or_(Execution.host_id == host_id, Execution.host_id.is_(None)))
            else:
                out_stmt = out_stmt.where(Execution.host_id.is_(None))

            ex_outputs = await db.execute(out_stmt)
            for output, exec_id, exec_item_id, exec_item_name in ex_outputs.all():
                typed = output.get_typed_value()
                # Global namespace: only fill keys the caller did NOT already provide, so
                # a stale stored output can't silently override an operator-supplied
                # {target}/{port} for this run. Namespaced {item_id.key} stays authoritative.
                variables.setdefault(output.key, typed)
                # Key by execution id — that is what templating.py resolves {12.key} against
                # (and what flow_manager passes). The item-id alias is kept so templates
                # written against the older numbering keep working.
                execution_outputs.setdefault(exec_id, {})[output.key] = typed
                execution_outputs.setdefault(exec_item_id, {}).setdefault(output.key, typed)
                # {Item Name.key} / {latest:Item Name.key}: newest execution per item name wins.
                if exec_item_name and exec_id >= item_name_to_execution.get(exec_item_name, 0):
                    item_name_to_execution[exec_item_name] = exec_id

        # Resolve {targets} from host tags if item has target_filter (and user did not supply targets)
        if project_id and is_unset(variables.get("targets")):
            target_filter = getattr(item, "target_filter", None) or {}
            filter_tags = (target_filter.get("tags") or []) if isinstance(target_filter, dict) else []
            if variables.get("target_filter_tags"):
                filter_tags = variables["target_filter_tags"]
            if filter_tags:
                placeholders = ", ".join([f":tag_{i}" for i in range(len(filter_tags))])
                tag_filter = text(
                    f"EXISTS (SELECT 1 FROM json_each(hosts.tags) "
                    f"WHERE json_each.value IN ({placeholders}))"
                )
                params = {f"tag_{i}": t for i, t in enumerate(filter_tags)}
                stmt = (
                    select(Host.ip_address)
                    .where(
                        Host.project_id == project_id,
                        Host.ip_address.isnot(None),
                        Host.deleted_at.is_(None),
                        Host.excluded.is_(False),
                        tag_filter,
                    )
                    .limit(5000)
                )
                hosts_result = await db.execute(stmt, params)
                resolved_ips = [r[0] for r in hosts_result.all() if r[0]]
                if resolved_ips:
                    variables["targets"] = resolved_ips


        # Prepare arguments based on schema (File vs String enforcement)
        version = item.next_version
        
        # We need the execution ID for unique filenames, but execution is created after command is rendered?
        # Actually execution needs command to be created.
        # So we'll use a placeholder or pre-generate ID logic? OR just use randomness.
        # But wait, we create execution object at line 106.
        # I can create the execution first with empty command, then update it?
        # Or just generate a unique ID for the file here.
        
        # Let's generate a temporary ID for file naming if execution.id isn't available yet.
        # Or better: create execution, flush to get ID, then prepare args, then update command.
        
        
        # Look up Project Variable types to enforce global defaults (e.g. scope=File)
        from app.models.variable import ProjectVariable
        project_id = item.group.project_id
        
        # Get all project variables for this project
        stmt = select(ProjectVariable).where(ProjectVariable.project_id == project_id)
        result = await db.execute(stmt)
        proj_vars = result.scalars().all()
        
        # Build a schema from project variables
        project_schema = {}
        for pv in proj_vars:
            if pv.var_type == "file":
                project_schema[pv.key] = {"type": "file"}
            # else default string, no schema needed
        
        # Merge schemas: Item schema overrides Project schema
        item_schema = item.parameter_schema or {}
        combined_schema = {**project_schema, **item_schema}
        
        # Either reuse existing execution record or create a new one
        if execution_id_override:
            result = await db.execute(
                select(Execution).where(Execution.id == execution_id_override)
            )
            execution = result.scalar_one_or_none()
            if not execution:
                raise ValueError(f"Execution {execution_id_override} not found")
            # Update the existing record
            execution.variables_used = variables
        else:
            execution = Execution(
                item_id=item_id, host_id=host_id, version=version,
                status=ExecutionStatus.PENDING, command="", variables_used=variables,
                flow_execution_id=flow_execution_id, flow_step_index=flow_step_index
            )
            db.add(execution)
            await db.flush()  # Get ID
        
        # Prepare variables using COMBINED schema
        prepared_vars = await self._prepare_command_arguments(variables, combined_schema, execution.id)
        
        # Render command with PREPARED variables (paths replaced)
        # Use override if provided, else use item template
        template_str = command_override if command_override else item.command_template
        
        if not template_str:
             # Fallback or error?
             template_str = ""
             
        unresolved: List[str] = []
        command = template_engine.render(
            template_str,
            variables=prepared_vars,
            execution_outputs=execution_outputs,
            item_name_to_execution=item_name_to_execution,
            latest_item_name_to_execution=item_name_to_execution,
            unresolved=unresolved,
        )

        execution.command = command
        # Update variables_used to show resolved values? Or keep original?
        # Keeping original in variables_used is better for audit, but maybe store effective in logs.

        # execution already added

        context = TaskContext(execution_id=execution.id)
        if unresolved:
            # A visible warning beats a command that quietly runs with a dangling flag.
            # Primed on the buffer (not execution.stderr) because _run_subprocess assigns
            # execution.stderr = context.stderr_buffer when the run finishes.
            note = f"Unresolved variables: {', '.join(unresolved)}"
            print(f"[Orchestrator] {note} (execution {execution.id})")
            context.stderr_buffer = f"[Warning] {note}\n"
            execution.stderr = context.stderr_buffer
        async with self._lock:
            self._running_tasks[execution.id] = context

        # Set RUNNING and persist before acquiring semaphore so the UI updates even when
        # we are queued behind other executions (avoids executions stuck in PENDING).
        execution.status = ExecutionStatus.RUNNING
        execution.started_at = datetime.utcnow()

        if commit_transaction:
            await db.commit()
            await db.refresh(execution)
        else:
            await db.flush()

        try:
            payload = await execution_sync_payload(db, execution)
            await sync_service.record_event(
                db,
                entity_type="executions",
                entity_public_id=execution.public_id,
                operation="update",
                payload=payload,
            )
            if commit_transaction:
                await db.commit()
            else:
                await db.flush()
        except Exception as e:
            print(f"[Orchestrator] Sync record (running) failed: {e}")

        await notification_manager.send_execution_status(
            execution.id,
            "running",
            details={"command": command},
            item_id=item_id
        )

        # Determine CWD before entering semaphore (used by _run_subprocess)
        cwd = None
        if item.group and item.group.project:
            from app.core.utils import resolve_project_path
            cwd = resolve_project_path(item.group.project)

        try:
            async with self._semaphore:
                await self._run_subprocess(execution, context, item, db, item.timeout, output_callback, cwd=cwd)
        except asyncio.CancelledError:
            execution.status = ExecutionStatus.CANCELLED
        except asyncio.TimeoutError:
            execution.status = ExecutionStatus.TIMEOUT
        except Exception as e:
            execution.status = ExecutionStatus.FAILED
            execution.stderr = (execution.stderr or "") + f"\nError: {str(e)}"
        finally:
            execution.completed_at = datetime.utcnow()
            if execution.status == ExecutionStatus.RUNNING:
                # User cancel breaks the stream without raising CancelledError, and the
                # agent reports timeouts via a "[Timeout]" stderr marker + exit -1.
                # Distinguish both so the UI doesn't show every one as a generic FAILED.
                if context.cancelled:
                    execution.status = ExecutionStatus.CANCELLED
                elif execution.exit_code == 0:
                    execution.status = ExecutionStatus.COMPLETED
                elif execution.stderr and "[Timeout]" in execution.stderr:
                    execution.status = ExecutionStatus.TIMEOUT
                else:
                    execution.status = ExecutionStatus.FAILED
            
            parsed = {}
            if execution.stdout and item.output_regex:
                # Use HostRunner for command extraction
                async def executor_wrapper(cmd: str, cwd: Optional[str]) -> Dict[str, Any]:
                    return await host_runner.execute_sync(cmd, cwd=cwd)

                parsed = await output_parser.parse_output(
                    execution.stdout,
                    item.output_regex,
                    cwd=cwd,
                    command_executor=executor_wrapper,
                )
                execution.parsed_output = output_parser.to_json(parsed)
                for key, res in parsed.items():
                    import json
                    stored_value = res.value
                    if isinstance(res.value, (list, dict)):
                        stored_value = json.dumps(res.value)
                    else:
                        stored_value = str(res.value)
                        
                    db.add(ExecutionOutput(execution_id=execution.id, key=key, value=stored_value, data_type=res.data_type))
                
                # Variable Injection requires parsed output
                await self._process_variable_injection(db, execution, item, parsed)

            if execution.stdout:
                # Share/file discovery and asset discovery run regardless of output_regex
                await self._process_discovered_files(db, execution, parsed)
                await self._process_asset_discovery(db, execution)

            # Persist alert matches as structured, deduped Findings — survives restart
            # (unlike the in-memory Alert) and feeds the report. Same txn as the output.
            if execution.alerts_triggered:
                try:
                    from app.core.findings import ingest_execution_findings
                    await ingest_execution_findings(db, execution, item)
                except Exception as fe:
                    print(f"[Orchestrator] Finding ingest failed: {fe}")

            async with self._lock:
                self._running_tasks.pop(execution.id, None)

            # Commit changes (output, status) BEFORE notifying listeners
            # This ensures FlowManager sees the COMPLETED status in its own session
            await db.commit()
            await db.refresh(execution)
            try:
                payload = await execution_sync_payload(db, execution)
                await sync_service.record_event(
                    db,
                    entity_type="executions",
                    entity_public_id=execution.public_id,
                    operation="update",
                    payload=payload,
                )
                await db.commit()
            except Exception as e:
                print(f"[Orchestrator] Sync record (completed) failed: {e}")

            # SYNC TO PROJECT CHECKLIST (User Request)
            # Ensure outputs are loaded before sync (execution may be expired after commit).
            await db.refresh(execution, attribute_names=["outputs"])
            try:
                await self._sync_execution_to_project_item(db, execution, item)
            except Exception as e:
                print(f"[Orchestrator] Sync failed (non-critical): {e}")

            await notification_manager.send_execution_status(
                execution.id,
                execution.status.value,
                details={"exit_code": execution.exit_code},
                item_id=execution.item_id
            )

        # Clean up temporary files asynchronously
        try:
            import os
            import glob

            temp_dir = settings.STORAGE_PATH / "tmp"
            temp_dir_str = str(temp_dir)

            batch_file = os.path.join(
                temp_dir_str, f"batch_{execution.id}_targets.txt"
            )
            if await asyncio.to_thread(os.path.exists, batch_file):
                await asyncio.to_thread(os.remove, batch_file)
                print(f"[Orchestrator] Cleaned up batch file: {batch_file}")

            pattern_str = os.path.join(temp_dir_str, f"exec_{execution.id}_*")
            matching_files = await asyncio.to_thread(glob.glob, pattern_str)
            for fpath in matching_files:
                try:
                    await asyncio.to_thread(os.remove, fpath)
                    print(f"[Orchestrator] Cleaned up temp variable file: {fpath}")
                except Exception as e:
                    print(f"[Orchestrator] Error deleting {fpath}: {e}")
        except Exception as e:
            print(f"[Orchestrator] Error during file cleanup: {e}")
        
        return execution

    async def _sync_execution_to_project_item(self, db: AsyncSession, execution: Execution, original_item: ChecklistItem):
        """
        Sync execution results from a Template Item (in Flow) to a matching Project Item (in Checklist).
        """
        # 1. Check if original item is a template (no project_id in its group)
        if not original_item.group or original_item.group.project_id:
            return  # Not a template or already a project item

        # 2. Identify Target Project ID
        # We can get it from variables_used OR from the Flow execution context?
        # execute_item logic: "if item.group.project_id... else..."
        # We need the project_id where this flow is running.
        # It should be in execution.variables_used["project_id"] (injected by FlowManager)
        # OR we can infer it from the Host if host_id is present.
        
        target_project_id = execution.variables_used.get("project_id")
        
        if not target_project_id and execution.host_id:
             # Try to get from Host
             from app.models.host import Host
             result = await db.execute(select(Host).where(Host.id == execution.host_id))
             host = result.scalar_one_or_none()
             if host:
                 target_project_id = host.project_id
        
        if not target_project_id:
            # print(f"[Orchestrator] Could not identify project_id for sync. exec_id={execution.id}")
            return

        # 3. Find Matching Project Checklist Item
        # Criteria: Same Item Name AND Same Group Name
        # We need to join Item -> Group
        
        stmt = (
            select(ChecklistItem)
            .join(ChecklistGroup)
            .where(
                ChecklistGroup.project_id == target_project_id,
                ChecklistGroup.name == original_item.group.name,
                ChecklistItem.name == original_item.name
            )
        )
        result = await db.execute(stmt)
        project_item = result.scalar_one_or_none()
        
        if not project_item:
            # print(f"[Orchestrator] No matching project item found for sync. Project {target_project_id}, Item '{original_item.name}'")
            return

        # 4. Clone Execution Record
        # We create a NEW execution record linked to the Project Item
        try:
            print(f"[Orchestrator] Syncing execution {execution.id} to Project Item {project_item.id}")
            
            new_exec = Execution(
                item_id=project_item.id,
                host_id=execution.host_id,
                version=project_item.next_version,
                status=execution.status,
                command=execution.command,
                variables_used=execution.variables_used, # Keep same variables or filter? Keep.
                stdout=execution.stdout,
                stderr=execution.stderr,
                exit_code=execution.exit_code,
                parsed_output=execution.parsed_output,
                started_at=execution.started_at,
                completed_at=execution.completed_at,
                # Link to same flow context? 
                # Maybe modify flow_execution_id to indicate it's a sync? Or keep it?
                # If we keep it, FlowViewer might duplicate? 
                # FlowViewer queries by flow_execution_id. 
                # If we have 2 executions with same flow_execution_id, it might get confused or show both.
                # FlowViewer usually filters by FlowStep... wait. 
                # Project Items are NOT in the Flow Definition (which uses Template Items).
                # So FlowManager won't see this new execution as part of the flow steps.
                # So it's safe to keep flow_execution_id for traceabiltiy, 
                # OR set it to None to avoid confusion.
                # Let's set it to None or a special flag.
                flow_execution_id=f"sync_from_{execution.id}", 
                # flow_step_index=None
            )
            db.add(new_exec)
            await db.flush()
            
            # 5. Clone Outputs
            # execution.outputs has lazy="raise"; use inspect().unloaded to avoid lazy-load.
            outputs_loaded = 'outputs' not in sa_inspect(execution).unloaded
            if not outputs_loaded:
                await db.refresh(execution, attribute_names=['outputs'])

            for out in execution.outputs:
                new_out = ExecutionOutput(
                    execution_id=new_exec.id,
                    key=out.key,
                    value=out.value,
                    data_type=out.data_type
                )
                db.add(new_out)
            
            await db.flush()
            print(f"[Orchestrator] Synced execution created: {new_exec.id}")

            # Run asset processing on the synced execution.
            # The original execution skipped these because the template item
            # has no project_id.  The cloned execution points at a project
            # item, so the guard clauses will now pass.
            try:
                await self._process_asset_discovery(db, new_exec)
            except Exception as ad_err:
                print(f"[Orchestrator] Asset discovery on synced exec failed: {ad_err}")

            try:
                await self._process_discovered_files(db, new_exec, {})
            except Exception as df_err:
                print(f"[Orchestrator] Discovered-files on synced exec failed: {df_err}")

            try:
                if new_exec.stdout and project_item.output_regex:
                    async def _executor(cmd: str, cwd: Optional[str] = None) -> Dict[str, Any]:
                        return await host_runner.execute_sync(cmd, cwd=cwd)

                    synced_parsed = await output_parser.parse_output(
                        new_exec.stdout,
                        project_item.output_regex,
                        command_executor=_executor,
                    )
                    await self._process_variable_injection(
                        db, new_exec, project_item, synced_parsed
                    )
            except Exception as vi_err:
                print(f"[Orchestrator] Variable injection on synced exec failed: {vi_err}")

            await db.commit()

            # Notify Project Update (so Checklist UI updates)
            await notification_manager.send_project_update(
                "item_updated",
                target_project_id,
                {"item_id": project_item.id}
            )

        except Exception as e:
            print(f"[Orchestrator] Error syncing execution: {e}")
            # Do not fail the original execution

    # Permission hierarchy for "most permissive wins" share update logic
    _ACCESS_PRIORITY = {"no_access": 0, "unknown": 1, "read": 2, "read_write": 3}

    async def _process_discovered_files(self, db: AsyncSession, execution: Execution, parsed_results: Dict[str, Any]):
        """Process output for file system discoveries using ShareParser."""
        import re as _re
        from app.core.share_parser import share_parser, ShareType
        
        if not execution.stdout or not execution.host_id:
            return
        
        stdout = execution.stdout
        command_lower = execution.command.lower()
        
        # --- Tool detection ---
        # Share-listing tools (NOT smbclient; handled separately below)
        share_discovery_tools = [
            'smbmap', 'nxc', 'netexec', 'crackmapexec', 'showmount', 'enum4linux',
        ]
        is_share_discovery = any(tool in command_lower for tool in share_discovery_tools)

        # smbclient -L is share listing; smbclient //host/share -c "..." is file listing
        is_smbclient_share_listing = (
            'smbclient' in command_lower and '-l' in command_lower
        )
        is_smbclient_file_listing = (
            'smbclient' in command_lower and not is_smbclient_share_listing
        )
        if is_smbclient_share_listing:
            is_share_discovery = True

        is_smbmap_recursive = (
            'smbmap' in command_lower
            and ('-r' in command_lower or '--depth' in command_lower)
        )

        file_listing_tools = ['ls', 'ftp', 'dir']
        is_file_listing = (
            is_smbclient_file_listing
            or any(tool in command_lower for tool in file_listing_tools)
        )
        
        try:
            new_shares_count = 0
            updated_shares_count = 0
            total_new_files = 0

            # 1. Parse shares (SMBMap, NetExec, smbclient -L, showmount, enum4linux)
            if is_share_discovery:
                detected_tool = share_parser.detect_tool(stdout)
                shares = share_parser.parse_shares(stdout, tool=detected_tool)
                
                existing_shares_result = await db.execute(
                    select(DiscoveredFile).where(
                        DiscoveredFile.host_id == execution.host_id,
                        DiscoveredFile.file_type == "share"
                    )
                )
                existing_shares = {s.share_name: s for s in existing_shares_result.scalars().all()}
                
                for share_info in shares:
                    is_interesting, interest_reason = DiscoveredFile.is_interesting_file(share_info.share_name)
                    
                    if share_info.share_name in existing_shares:
                        existing = existing_shares[share_info.share_name]
                        new_access = share_info.access_level.value
                        existing_access = (existing.permissions or {}).get("access_level", "unknown")

                        # "Most permissive wins" -- only escalate, never downgrade
                        if self._ACCESS_PRIORITY.get(new_access, 0) > self._ACCESS_PRIORITY.get(existing_access, 0):
                            existing.permissions = {
                                "access_level": new_access,
                                "share_type": share_info.share_type.value,
                            }
                            existing.is_readable = new_access in ("read", "read_write")
                            existing.is_writable = new_access == "read_write"
                            updated_shares_count += 1

                        if share_info.comment:
                            existing.extra_data = {
                                **existing.extra_data,
                                "comment": share_info.comment,
                            }
                    else:
                        share_file = DiscoveredFile(
                            host_id=execution.host_id,
                            execution_id=execution.id,
                            share_name=share_info.share_name,
                            path="/",
                            name=share_info.share_name,
                            file_type="share",
                            size=None,
                            is_readable=share_info.access_level.value in ("read", "read_write"),
                            is_writable=share_info.access_level.value == "read_write",
                            is_interesting=is_interesting,
                            interest_reason=interest_reason,
                            permissions={
                                "access_level": share_info.access_level.value,
                                "share_type": share_info.share_type.value,
                            },
                            extra_data={
                                "comment": share_info.comment,
                                "allowed_hosts": share_info.allowed_hosts,
                                **share_info.extra_data
                            }
                        )
                        db.add(share_file)
                        await db.flush()
                        await db.refresh(share_file)
                        existing_shares[share_info.share_name] = share_file
                        new_shares_count += 1
                        try:
                            payload = await discovered_file_sync_payload(db, share_file)
                            await sync_service.record_event(
                                db,
                                entity_type="discovered_files",
                                entity_public_id=share_file.public_id,
                                operation="create",
                                payload=payload,
                            )
                        except Exception as e:
                            print(f"[Orchestrator] Sync record (discovered_file share) failed: {e}")
                
                if new_shares_count or updated_shares_count:
                    print(f"[ShareParser] Discovered {new_shares_count} new shares, updated {updated_shares_count} from execution {execution.id}")
            
            # 2. SMBMap recursive file listings (with dedup)
            if is_smbmap_recursive:
                files_by_share = share_parser.parse_smbmap_files(stdout)

                # Pre-fetch existing files for this host to deduplicate
                existing_files_result = await db.execute(
                    select(DiscoveredFile).where(
                        DiscoveredFile.host_id == execution.host_id,
                        DiscoveredFile.file_type != "share"
                    )
                )
                existing_files = {
                    (f.share_name, f.path, f.name): f
                    for f in existing_files_result.scalars().all()
                }
                
                for share_name, files in files_by_share.items():
                    for entry in files:
                        is_interesting, interest_reason = DiscoveredFile.is_interesting_file(entry.name)
                        path = entry.extra_data.get("share_path", "/")
                        dedup_key = (share_name, path, entry.name)

                        if dedup_key in existing_files:
                            existing_f = existing_files[dedup_key]
                            if entry.size is not None and entry.size != existing_f.size:
                                existing_f.size = entry.size
                                existing_f.execution_id = execution.id
                            continue

                        discovered_file = DiscoveredFile(
                            host_id=execution.host_id,
                            execution_id=execution.id,
                            share_name=share_name,
                            path=path,
                            name=entry.name,
                            file_type=entry.file_type,
                            size=entry.size,
                            is_readable=entry.is_readable,
                            is_writable=entry.is_writable,
                            is_interesting=is_interesting,
                            interest_reason=interest_reason,
                            permissions={"raw": entry.permissions},
                            extra_data={
                                "date": entry.date,
                                "hidden": entry.is_hidden,
                            }
                        )
                        db.add(discovered_file)
                        await db.flush()
                        await db.refresh(discovered_file)
                        existing_files[dedup_key] = discovered_file
                        total_new_files += 1
                        try:
                            payload = await discovered_file_sync_payload(db, discovered_file)
                            await sync_service.record_event(
                                db,
                                entity_type="discovered_files",
                                entity_public_id=discovered_file.public_id,
                                operation="create",
                                payload=payload,
                            )
                        except Exception as e:
                            print(f"[Orchestrator] Sync record (discovered_file) failed: {e}")
                
                if total_new_files:
                    print(f"[ShareParser] Discovered {total_new_files} files from SMBMap execution {execution.id}")
            
            # 3. File listings from smbclient (non -L), ftp, ls, etc.
            elif is_file_listing and not is_share_discovery:
                detected_tool = share_parser.detect_tool(stdout)
                files = share_parser.parse_files(stdout, tool=detected_tool)
                
                share_name = "UNKNOWN"
                if "share_name" in parsed_results:
                    share_name = parsed_results["share_name"].value
                else:
                    share_match = _re.search(r'//[^/]+/(\S+)', execution.command)
                    if share_match:
                        share_name = share_match.group(1)
                
                for entry in files:
                    file_data = share_parser.to_discovered_file_data(entry, share_name, path="/")
                    
                    discovered_file = DiscoveredFile(
                        host_id=execution.host_id,
                        execution_id=execution.id,
                        **file_data
                    )
                    db.add(discovered_file)
                    await db.flush()
                    await db.refresh(discovered_file)
                    total_new_files += 1
                    try:
                        payload = await discovered_file_sync_payload(db, discovered_file)
                        await sync_service.record_event(
                            db,
                            entity_type="discovered_files",
                            entity_public_id=discovered_file.public_id,
                            operation="create",
                            payload=payload,
                        )
                    except Exception as e:
                        print(f"[Orchestrator] Sync record (discovered_file listing) failed: {e}")
                
                if files:
                    print(f"[ShareParser] Discovered {len(files)} files from execution {execution.id}")

            # 4. Send WebSocket notification when shares/files were discovered
            if new_shares_count or updated_shares_count or total_new_files:
                try:
                    item_result = await db.execute(
                        select(ChecklistItem)
                        .where(ChecklistItem.id == execution.item_id)
                        .options(selectinload(ChecklistItem.group))
                    )
                    item_obj = item_result.scalar_one_or_none()
                    if item_obj and item_obj.group and item_obj.group.project_id:
                        await notification_manager.send_project_update(
                            "shares_discovered",
                            item_obj.group.project_id,
                            {
                                "host_id": execution.host_id,
                                "new_shares": new_shares_count,
                                "updated_shares": updated_shares_count,
                                "new_files": total_new_files,
                            },
                        )
                except Exception as e:
                    print(f"[Orchestrator] Share discovery notification failed: {e}")
                    
        except Exception as e:
            print(f"[ShareParser] Error parsing output: {e}") 

    async def _process_variable_injection(self, db: AsyncSession, execution: Execution, item: ChecklistItem, parsed_results: Dict[str, Any]):
        """Inject parsed outputs into Project Variables based on policy."""
        policy = item.storage_policy or {}
        mapping = policy.get("variable_mapping", {})
        
        print(f"[DEBUG] Processing variable injection for item {item.id}. Mapping: {mapping}")

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
            
            stmt = select(ProjectVariable).where(
                ProjectVariable.project_id == project_id,
                ProjectVariable.key == var_key,
                ProjectVariable.host_id == target_host_id
            )
            result = await db.execute(stmt)
            # .first() (not scalar_one_or_none) so pre-existing duplicate rows for the
            # same (project,key,host) scope don't raise MultipleResultsFound and crash
            # the finalizer. ponytail: add a unique constraint + dedup migration later.
            existing = result.scalars().first()

            if existing:
                existing.value = val
                await db.flush()
                await db.refresh(existing)
                try:
                    payload = await project_variable_sync_payload(db, existing)
                    await sync_service.record_event(
                        db,
                        entity_type="project_variables",
                        entity_public_id=existing.public_id,
                        operation="update",
                        payload=payload,
                    )
                except Exception as e:
                    print(f"[Orchestrator] Sync record (project_variable update) failed: {e}")
            else:
                new_var = ProjectVariable(project_id=project_id, key=var_key, value=val, host_id=target_host_id)
                db.add(new_var)
                await db.flush()
                await db.refresh(new_var)
                try:
                    payload = await project_variable_sync_payload(db, new_var)
                    await sync_service.record_event(
                        db,
                        entity_type="project_variables",
                        entity_public_id=new_var.public_id,
                        operation="create",
                        payload=payload,
                    )
                except Exception as e:
                    print(f"[Orchestrator] Sync record (project_variable create) failed: {e}")
                
            updates_to_notify.append(var_key)
        
        if updates_to_notify:
            await db.flush()
            
            for vk in updates_to_notify:
                 await notification_manager.send_project_update(
                     "variable_updated",
                     project_id,
                     {"key": vk, "host_id": execution.host_id}
                 )
    
    def _to_host_path(self, container_path: str) -> str:
        """Translate a container-side storage path to the host-agent-accessible path."""
        host_storage = settings.HOST_STORAGE_PATH
        if not host_storage:
            return container_path
        container_storage = str(settings.STORAGE_PATH)
        if container_path.startswith(container_storage):
            return host_storage + container_path[len(container_storage):]
        return container_path

    async def _prepare_command_arguments(self, variables: Dict[str, Any], schema: Dict[str, Any], execution_id: int) -> Dict[str, Any]:
        """
        Process variables according to schema (File creation vs String joining).
        Returns a new dictionary with prepared values (e.g. file paths).
        """
        prepared = variables.copy()
        import os
        
        temp_dir = settings.STORAGE_PATH / "tmp"
        os.makedirs(temp_dir, exist_ok=True)
        
        # 1. SPECIAL CASE: "targets" (plural) variable
        # If present and list/string, ALWAYS treat as file-based batch target list
        if not is_unset(variables.get("targets")):
            raw_targets = variables["targets"]
            targets_list: List[Any] = []
            if isinstance(raw_targets, list):
                targets_list = raw_targets
            elif isinstance(raw_targets, str):
                targets_list = [t.strip() for t in raw_targets.split() if t.strip()]

            if targets_list:
                filename = f"batch_{execution_id}_targets.txt"
                filepath = os.path.join(str(temp_dir), filename)
                content = "\n".join(str(v) for v in targets_list)
                try:
                    import aiofiles
                    async with aiofiles.open(filepath, "w") as f:
                        await f.write(content)
                    prepared["targets"] = self._to_host_path(filepath)
                except Exception as e:
                    print(f"[Orchestrator] Failed to create batch targets file: {e}")

        for key, value in variables.items():
            if key == "targets": continue # Handled above

            config = schema.get(key, {})
            param_type = config.get("type", "string") # default to string
            
            if param_type == "file":
                # Create a temporary file
                ext = config.get("extension", ".txt")
                if not ext.startswith("."):
                    ext = f".{ext}"
                
                filename = f"exec_{execution_id}_{key}{ext}"
                filepath = os.path.join(str(temp_dir), filename)
                
                content = ""
                if isinstance(value, list):
                    content = "\n".join(str(v) for v in value)
                else:
                    content = str(value)
                    
                try:
                    import aiofiles
                    async with aiofiles.open(filepath, "w") as f:
                        await f.write(content)
                    
                    prepared[key] = self._to_host_path(filepath)
                    print(f"[Orchestrator] Created temp variable file: {filepath} -> cmd path: {prepared[key]}")
                except Exception as e:
                    print(f"[Orchestrator] Failed to create temp file for {key}: {e}")
                    
            elif param_type == "string":
                # Handle list joining
                if isinstance(value, list):
                    delimiter = config.get("delimiter", " ")
                    if delimiter == "newline":
                        delimiter = "\n"
                    elif delimiter == "comma":
                        delimiter = ","
                    # default space
                    prepared[key] = delimiter.join(str(v) for v in value)
        
        return prepared

    async def _process_asset_discovery(self, db: AsyncSession, execution: Execution):
        """Global scan for IP addresses in output to auto-create Hosts."""
        import re
        from app.models.host import Host
        
        if not execution.stdout:
            return

        # 0. Get Project ID (REQUIRED for host creation/lookup)
        result = await db.execute(
             select(ChecklistItem)
             .where(ChecklistItem.id == execution.item_id)
             .options(selectinload(ChecklistItem.group))
        )
        item = result.scalar_one_or_none()
        if not item or not item.group or not item.group.project_id:
            return
            
        project_id = item.group.project_id

        # 1. New Service & Host Info Parsing (Nmap, Rustscan, NetExec)
        try:
            # Determine default_ip and execution_host (host this execution was run for)
            default_ip = None
            execution_host = None
            if execution.host_id:
                if 'host' in execution.__dict__ and execution.host:
                    execution_host = execution.host
                    default_ip = execution_host.ip_address
                else:
                    stmt = select(Host).where(Host.id == execution.host_id)
                    res = await db.execute(stmt)
                    execution_host = res.scalar_one_or_none()
                    if execution_host:
                        default_ip = execution_host.ip_address

            parsed_data = service_parser.parse(execution.command, execution.stdout, default_ip=default_ip)

            if parsed_data.hosts or parsed_data.services:
                print(f"[Orchestrator] Service Parsing Result: Hosts={list(parsed_data.hosts.keys())}, Services={sum(len(v) for v in parsed_data.services.values())}")

            # Batch-fetch existing hosts and services to avoid N+1 queries
            all_ips = list(parsed_data.hosts.keys())
            existing_hosts_result = await db.execute(
                select(Host).where(
                    Host.project_id == project_id,
                    Host.ip_address.in_(all_ips)
                )
            )
            hosts_map = {h.ip_address: h for h in existing_hosts_result.scalars().all()}

            # Include execution host so we load its services and prefer it when applying (same IP can exist on multiple hosts)
            known_host_ids = list({h.id for h in hosts_map.values()})
            if execution_host and execution_host.ip_address in all_ips and execution_host.id not in known_host_ids:
                known_host_ids.append(execution_host.id)
            services_map = {}
            if known_host_ids:
                existing_svcs_result = await db.execute(
                    select(Service).where(Service.host_id.in_(known_host_ids))
                )
                for s in existing_svcs_result.scalars().all():
                    services_map[(s.host_id, s.port, s.protocol)] = s

            # Process Hosts using pre-fetched maps; prefer execution host when scan was run for that host (same IP)
            for ip, host_info in parsed_data.hosts.items():
                if execution_host and execution_host.ip_address == ip:
                    host = execution_host
                else:
                    host = hosts_map.get(ip)

                if not host:
                    print(f"Auto-discovering host from service parser: {ip}")
                    host = Host(
                        project_id=project_id,
                        ip_address=ip,
                        extra_data={"discovery_method": "auto_service_scan"}
                    )
                    db.add(host)
                    await db.flush()
                    await db.refresh(host)
                    hosts_map[ip] = host
                    try:
                        payload = await host_sync_payload(db, host)
                        await sync_service.record_event(
                            db,
                            entity_type="hosts",
                            entity_public_id=host.public_id,
                            operation="create",
                            payload=payload,
                        )
                    except Exception as e:
                        print(f"[Orchestrator] Sync record (asset discovery host) failed: {e}")
                    await notification_manager.send_project_update(
                        "host_created", project_id, {"ip_address": ip, "id": host.id}
                    )
                
                updated = False
                if host_info.hostname and host.hostname != host_info.hostname:
                    host.hostname = host_info.hostname
                    updated = True
                if host_info.os_info and (not host.os_info or len(host_info.os_info) > len(host.os_info)):
                    host.os_info = host_info.os_info
                    updated = True
                if host_info.domain:
                    if not host.fqdn:
                        host.fqdn = host_info.domain
                        updated = True
                    current_extra = host.extra_data.copy() if host.extra_data else {}
                    if current_extra.get('domain') != host_info.domain:
                        current_extra['domain'] = host_info.domain
                        host.extra_data = current_extra
                        updated = True
                
                if host_info.extra_data:
                    current_extra = host.extra_data.copy() if host.extra_data else {}
                    has_changes = False
                    for k, v in host_info.extra_data.items():
                        if k not in current_extra or current_extra[k] != v:
                            current_extra[k] = v
                            has_changes = True
                    
                    if has_changes:
                        host.extra_data = current_extra
                        updated = True
                
                if updated:
                    db.add(host)
                    await db.flush()
                    await db.refresh(host)
                    try:
                        payload = await host_sync_payload(db, host)
                        await sync_service.record_event(
                            db,
                            entity_type="hosts",
                            entity_public_id=host.public_id,
                            operation="update",
                            payload=payload,
                        )
                    except Exception as e:
                        print(f"[Orchestrator] Sync record (asset discovery host update) failed: {e}")
                    await notification_manager.send_project_update(
                        "host_updated", project_id, {"id": host.id}
                    )
                
                # Process Services using pre-fetched map (dedupe by port/protocol to avoid last-one-wins)
                services = _merge_duplicate_services(parsed_data.services.get(ip, []))
                for svc in services:
                    existing_svc = services_map.get((host.id, svc.port, svc.protocol))
                    
                    if existing_svc:
                         updated_svc = False
                         if svc.state != existing_svc.state:
                             existing_svc.state = svc.state
                             updated_svc = True
                         if _should_update_service_name(existing_svc.name, svc.name):
                             existing_svc.name = svc.name
                             updated_svc = True
                         if svc.product and svc.product != existing_svc.product:
                             existing_svc.product = svc.product
                             updated_svc = True
                         if svc.version and svc.version != existing_svc.version:
                             existing_svc.version = svc.version
                             updated_svc = True
                         script_out = getattr(svc, "script_output", None)
                         if script_out:
                             current_extra = existing_svc.extra_data.copy() if existing_svc.extra_data else {}
                             nse_scripts = current_extra.get("nse_scripts", {})
                             nse_scripts.update(script_out)
                             current_extra["nse_scripts"] = nse_scripts
                             existing_svc.extra_data = current_extra
                             updated_svc = True
                         if updated_svc:
                             db.add(existing_svc)
                             await db.flush()
                             await db.refresh(existing_svc)
                             try:
                                 payload = await service_sync_payload(db, existing_svc)
                                 await sync_service.record_event(
                                     db,
                                     entity_type="services",
                                     entity_public_id=existing_svc.public_id,
                                     operation="update",
                                     payload=payload,
                                 )
                             except Exception as e:
                                 print(f"[Orchestrator] Sync record (service update) failed: {e}")
                    else:
                         # Create
                         extra = {}
                         script_out = getattr(svc, "script_output", None)
                         if script_out:
                             extra["nse_scripts"] = dict(script_out)
                         new_svc = Service(
                             host_id=host.id,
                             port=svc.port,
                             protocol=svc.protocol,
                             state=svc.state,
                             name=svc.name,
                             product=svc.product,
                             version=svc.version,
                             extra_data=extra,
                         )
                         db.add(new_svc)
                         await db.flush()
                         await db.refresh(new_svc)
                         try:
                             payload = await service_sync_payload(db, new_svc)
                             await sync_service.record_event(
                                 db,
                                 entity_type="services",
                                 entity_public_id=new_svc.public_id,
                                 operation="create",
                                 payload=payload,
                             )
                         except Exception as e:
                             print(f"[Orchestrator] Sync record (service create) failed: {e}")
                         services_map[(host.id, new_svc.port, new_svc.protocol)] = new_svc

                # Auto-tag host from its services (canonical tags for target filtering)
                all_service_names = {s.name for s in services if s.name}
                for (hid, _p, _proto), s in services_map.items():
                    if hid == host.id and s.name:
                        all_service_names.add(s.name)
                new_tags = set(host.tags or [])
                for name in all_service_names:
                    tag = SERVICE_TAG_MAP.get((name or "").lower())
                    if tag:
                        new_tags.add(tag)
                host.tags = sorted(new_tags)
                db.add(host)
                         
            # Commit after processing service parser
            if parsed_data.hosts or parsed_data.services:
                 await db.commit()
                 
        except Exception as e:
            print(f"[Orchestrator] Service parsing failed: {e}")
            import traceback
            traceback.print_exc()

            
        # 2. Generic IPv4 Discovery
        # Lookarounds forbid a leading/trailing digit or dot so 5+-segment version
        # strings (e.g. 1.2.3.4.5) and sub-matches don't register; ipaddress rejects
        # octets > 255 and reserved ranges.
        # ponytail: a bare 4-octet version like "4.15.0.32" is a valid IPv4 and still
        # slips through — acceptable residual for a fallback heuristic.
        import ipaddress
        ipv4_pattern = re.compile(r'(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])')
        ignore = {'127.0.0.1', '0.0.0.0', '255.255.255.255'}
        found_ips = set()
        for cand in ipv4_pattern.findall(execution.stdout):
            if cand in ignore:
                continue
            try:
                addr = ipaddress.ip_address(cand)
            except ValueError:
                continue
            if addr.is_loopback or addr.is_unspecified or addr.is_multicast or addr.is_reserved:
                continue
            found_ips.add(cand)
        
        if not found_ips:
            return
        
        # Check existing hosts
        # Optimization: Fetch all IP hosts for project
        existing_result = await db.execute(select(Host.ip_address).where(Host.project_id == project_id))
        existing_ips = set(existing_result.scalars().all())
        
        new_ips = found_ips - existing_ips
        
        for ip in new_ips:
            print(f"Auto-discovering host: {ip}")
            new_host = Host(
                project_id=project_id,
                ip_address=ip,
                # display_name is a property
                extra_data={"discovery_method": "auto_output_scan"}
            )
            db.add(new_host)
        
        if new_ips:
            await db.flush()
            result = await db.execute(
                select(Host).where(
                    Host.project_id == project_id,
                    Host.ip_address.in_(list(new_ips)),
                )
            )
            for new_host in result.scalars().all():
                try:
                    payload = await host_sync_payload(db, new_host)
                    await sync_service.record_event(
                        db,
                        entity_type="hosts",
                        entity_public_id=new_host.public_id,
                        operation="create",
                        payload=payload,
                    )
                except Exception as e:
                    print(f"[Orchestrator] Sync record (IPv4 discovery host) failed: {e}")
                # Notify frontend (same payload shape as the other host_created emitters)
                await notification_manager.send_project_update(
                    "host_created",
                    project_id,
                    {"ip_address": new_host.ip_address, "id": new_host.id},
                )

    async def _run_subprocess(
        self,
        execution: Execution,
        context: TaskContext,
        item: ChecklistItem,
        db: AsyncSession,
        timeout: int,
        output_callback: Optional[Callable] = None,
        cwd: Optional[str] = None,
    ):
        """Execute command via Host Agent."""

        effective_timeout = timeout or settings.TASK_TIMEOUT

        try:
            async for event in host_runner.execute_and_stream(
                execution.command,
                cwd=cwd,
                execution_id=str(execution.id),
                timeout=effective_timeout,
            ):
                if context.cancelled:
                    break
                    
                if "exit_code" in event:
                    execution.exit_code = event["exit_code"]
                    break
                
                stream = event.get("stream")
                text = event.get("text", "") + "\n"  # Add newline as SSE strips it often or we send lines
                
                if stream == "stdout":
                    context.stdout_buffer += text
                    execution.stdout = context.stdout_buffer # Real-time update? Expensive to write DB every line.
                    # Buffering optimized: write context, flush to DB periodically or at end.
                    # We only alert and notify here.
                    
                    if item.alert_patterns:
                        alerts = await notification_manager.process_output_chunk(execution.id, text, item.alert_patterns)
                        if alerts:
                            current = execution.alerts_triggered or []
                            current.extend([a.to_dict() for a in alerts])
                            execution.alerts_triggered = current
                            
                elif stream == "stderr":
                    context.stderr_buffer += text
                
                if output_callback:
                    output_callback(text)
                
                await notification_manager.notify("output", {
                    "execution_id": execution.id, 
                    "stream": stream, 
                    "text": text
                })
            
            execution.stdout = context.stdout_buffer
            execution.stderr = context.stderr_buffer
            
            if execution.exit_code is None:
                execution.exit_code = -1
                if context.cancelled:
                    # Cancel breaks the loop before the exit event — don't mislabel it
                    # as a lost connection.
                    execution.stderr += "\n[Cancelled] Execution cancelled by user."
                else:
                    execution.stderr += "\n[Error] Connection to Host Agent lost or no exit code received."

        except Exception as e:
            execution.exit_code = -1
            execution.stderr = (execution.stderr or "") + f"\n[Internal Error] {str(e)}"
            raise

    async def cancel_execution(self, execution_id: int) -> bool:
        # For now, local cancel flag only. Agent cancellation to be implemented.
        async with self._lock:
            context = self._running_tasks.get(execution_id)
        if not context:
            return False
        context.cancelled = True
        
        # Check if this is a parent execution (Batch)
        # We need to find all children and cancel them too
        # Since we don't track children in memory easily, we rely on DB or broad sweep
        # Best approach: Scan all running tasks, check if their variable "parent_execution_id" == execution_id
        
        async with self._lock:
             # Iterate over a copy of running tasks
             for eid, ctx in list(self._running_tasks.items()):
                 if eid == execution_id: continue
                 
                 # Check if this task is a child of the cancelled parent
                 # We need to access the execution object/variables. 
                 # Context doesn't store variables.
                 # OPTION: Store parent_id in TaskContext?
                 # OPTION: Query DB? Slow.
                 # OPTION: Just rely on the loop in _run_batch_execution to stop?
                 # The loop only checks before starting NEXT child. 
                 # We need to stop CURRENT running child.
                 
                 # Let's try to query DB for running children
                 pass

        # Call Host Runner to cancel process on agent (for the parent itself, likely valid if it was running a command, 
        # but for batch parent it's just a loop. The Loop will check context.cancelled.)
        
        # Also cancel the specific child that might be running
        # We can find it by querying Execution where parent_id = execution_id AND status = RUNNING
        from app.database import async_session_maker
        async with async_session_maker() as db:
             from app.models.execution import Execution
             # Find running children
             # We need to cast variable JSON lookup or just iterate in python if low volume
             # usage of encoded JSON in SQL is tricky for "variables_used->>parent_execution_id" depending on DB (SQLite vs PG)
             
             # Fallback: We know the loop in _run_batch_execution holds the lock or can track current child?
             # No, _run_batch_execution is async.
             
             # Simple approach: Loop all running tasks in this orchestrator memory, check their DB record
             # optimization: assume we don't have thousands of concurrent tasks
             
             running_ids = list(self._running_tasks.keys())
             if running_ids:
                 stmt = select(Execution).where(
                     Execution.id.in_(running_ids),
                     Execution.status == ExecutionStatus.RUNNING
                 )
                 result = await db.execute(stmt)
                 running_execs = result.scalars().all()
                 
                 for cx in running_execs:
                     parent_id = cx.variables_used.get("parent_execution_id")
                     if parent_id == execution_id:
                         # This is a child. Cancel it.
                         child_ctx = self._running_tasks.get(cx.id)
                         if child_ctx:
                             child_ctx.cancelled = True
                             await host_runner.cancel_execution(str(cx.id))
        
        await host_runner.cancel_execution(str(execution_id))
        
        return True
    
    async def execute_bulk(self, db: AsyncSession, item_ids: List[int], host_id: Optional[int] = None,
                           variables: Optional[Dict[str, Any]] = None, sequential: bool = False) -> List[Execution]:
        if sequential:
            executions, outputs = [], {}
            for item_id in item_ids:
                exec = await self.execute_item(db, item_id, host_id, variables, outputs, commit_transaction=True)
                executions.append(exec)
                if exec.parsed_output:
                    outputs[exec.id] = exec.parsed_output
            return executions
        else:
            from app.database import async_session_maker

            async def _execute_with_own_session(item_id: int) -> Execution:
                async with async_session_maker() as session:
                    try:
                        result = await self.execute_item(session, item_id, host_id, variables, commit_transaction=True)
                        await session.commit()
                        return result
                    except Exception:
                        await session.rollback()
                        raise

            results = await asyncio.gather(*[
                _execute_with_own_session(item_id)
                for item_id in item_ids
            ])
            return list(results)
    
    def get_running_executions(self) -> List[int]:
        return list(self._running_tasks.keys())


task_orchestrator = TaskOrchestrator()
