"""Flow manager for orchestrating dynamic checklists and pipelines."""
import asyncio
import ast
import operator as _operator
import re
import traceback
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, text
from sqlalchemy.orm import selectinload

_SAFE_CMP = {
    ast.Eq: _operator.eq, ast.NotEq: _operator.ne,
    ast.Lt: _operator.lt, ast.LtE: _operator.le,
    ast.Gt: _operator.gt, ast.GtE: _operator.ge,
    ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b,
}


def _coerce_num(v):
    """Coerce a numeric-looking string so "80" == 80 compares equal."""
    if isinstance(v, str):
        s = v.strip()
        try:
            return int(s)
        except ValueError:
            try:
                return float(s)
            except ValueError:
                return v
    return v


def safe_eval_condition(expr: str) -> bool:
    """Safely evaluate a simple boolean condition (comparisons + and/or/not + literals).

    Replaces eval(): no builtins, no attribute/call access. Numeric strings are coerced
    so "80" == 80 is True. Raises ValueError on anything unsupported (caller decides).
    """
    tree = ast.parse((expr or "").strip(), mode="eval")

    def ev(n):
        if isinstance(n, ast.BoolOp):
            vals = [ev(v) for v in n.values]
            return all(vals) if isinstance(n.op, ast.And) else any(vals)
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not):
            return not ev(n.operand)
        if isinstance(n, ast.Compare):
            left = ev(n.left)
            for op, comp in zip(n.ops, n.comparators):
                right = ev(comp)
                fn = _SAFE_CMP.get(type(op))
                if fn is None:
                    raise ValueError(f"operator {type(op).__name__} not allowed")
                if not fn(_coerce_num(left), _coerce_num(right)):
                    return False
                left = right
            return True
        if isinstance(n, ast.Constant):
            return n.value
        if isinstance(n, (ast.List, ast.Tuple)):
            return [ev(e) for e in n.elts]
        raise ValueError(f"unsupported expression node: {type(n).__name__}")

    return bool(ev(tree.body))

from app.models.flow import Flow, FlowStep
from app.models.flow_execution import FlowExecution
from app.models.execution import Execution, ExecutionStatus
from app.models.checklist import ChecklistItem
from app.core.orchestrator import task_orchestrator
from app.core.templating import template_engine


class FlowManager:
    def __init__(self):
        self._initialized = False
        self._flow_locks: Dict[str, asyncio.Lock] = {}
        self._flow_locks_guard = asyncio.Lock()

    async def _get_flow_lock(self, flow_execution_id: str) -> asyncio.Lock:
        """Per-flow-run lock so concurrent completion events can't double-schedule or
        stall the same flow's next stage."""
        async with self._flow_locks_guard:
            lock = self._flow_locks.get(flow_execution_id)
            if lock is None:
                lock = asyncio.Lock()
                self._flow_locks[flow_execution_id] = lock
            return lock

    async def initialize(self):
        if self._initialized:
            return
        from app.core.notifications import notification_manager
        await notification_manager.add_listener(self.handle_event)
        self._initialized = True
        print("[FlowManager] Initialized and listening for events.")

    # ------------------------------------------------------------------
    # start_flow
    # ------------------------------------------------------------------
    async def start_flow(
        self,
        db: AsyncSession,
        flow_id: int,
        variables: Optional[Dict[str, Any]] = None,
        flow_execution_id: Optional[str] = None,
        host_id: Optional[int] = None,
        target: Optional[str] = None,
        targets: Optional[List[str]] = None,
        host_ids: Optional[List[int]] = None,
        user_id: Optional[int] = None,
    ) -> Tuple[FlowExecution, List[Execution]]:
        variables = variables or {}

        result = await db.execute(select(Flow).where(Flow.id == flow_id))
        flow = result.scalar_one_or_none()
        if not flow:
            raise ValueError(f"Flow {flow_id} not found")

        # Resolve targets from host_ids if supplied
        if host_ids and not targets:
            from app.models.host import Host
            host_result = await db.execute(
                select(Host.ip_address).where(Host.id.in_(host_ids))
            )
            resolved = [r[0] for r in host_result.all() if r[0]]
            if resolved:
                targets = resolved

        # Auto-resolve targets from project hosts when none explicitly provided
        if not targets and not target and not host_id and not host_ids and flow.project_id:
            targets = await self._auto_resolve_targets(db, flow)

        # Merge single target into targets list when convenient
        merged_targets = list(targets or [])
        if target and target not in merged_targets:
            merged_targets.insert(0, target)

        # Create FlowExecution record
        import uuid
        if not flow_execution_id:
            flow_execution_id = str(uuid.uuid4())

        flow_exec = FlowExecution(
            id=flow_execution_id,
            flow_id=flow_id,
            status="running",
            variables=variables,
            target=target,
            targets=merged_targets,
            host_id=host_id,
            started_at=datetime.utcnow(),
            started_by_user_id=user_id,
        )
        db.add(flow_exec)
        await db.flush()

        print(f"[FlowManager] Starting Flow: {flow.name} ({flow_execution_id})")

        # Load steps
        step_result = await db.execute(
            select(FlowStep)
            .where(FlowStep.flow_id == flow_id)
            .order_by(FlowStep.order_index)
            .options(selectinload(FlowStep.checklist_item))
        )
        steps = step_result.scalars().all()
        if not steps:
            flow_exec.status = "completed"
            flow_exec.completed_at = datetime.utcnow()
            await db.flush()
            return flow_exec, []

        # Group by order_index — first stage
        initial_order_index = steps[0].order_index
        initial_steps = [s for s in steps if s.order_index == initial_order_index]

        # Load project variables with proper scoping (matches orchestrator logic)
        project_vars = await self._load_project_variables(
            db, flow.project_id,
            ad_domain_id=getattr(flow, "ad_domain_id", None),
            host_id=host_id,
        )

        base_vars = dict(project_vars)
        base_vars.update(variables)

        # Inject target variables
        self._inject_targets(base_vars, target, merged_targets)

        # Cancel stale executions for items we're about to run
        for step in initial_steps:
            stale_q = (
                select(Execution)
                .where(
                    Execution.item_id == step.checklist_item_id,
                    Execution.status.in_([ExecutionStatus.PENDING, ExecutionStatus.RUNNING]),
                )
            )
            if host_id is not None:
                stale_q = stale_q.where(Execution.host_id == host_id)
            else:
                stale_q = stale_q.where(Execution.host_id.is_(None))
            for stale in (await db.execute(stale_q)).scalars().all():
                print(f"[FlowManager] Cancelling stale execution {stale.id} for item {step.checklist_item_id}")
                stale.status = ExecutionStatus.CANCELLED
                stale.completed_at = datetime.utcnow()
                stale.stderr = (stale.stderr or "") + "\n[Auto-cancelled] Superseded by new flow execution."
        await db.flush()

        # Create executions for initial steps
        pending_tasks = []
        started_executions = []

        for step in initial_steps:
            step_vars = await self._resolve_step_variables(step, base_vars, {}, {})
            step_vars["flow_id"] = flow.id
            if flow.project_id:
                step_vars["project_id"] = flow.project_id

            if not self._evaluate_condition(step, base_vars, {}, {}):
                print(f"[FlowManager] Skipping step {step.order_index} due to condition.")
                continue

            # Resolve per-step targets based on target_mode
            step_host_id, step_target_vars = await self._resolve_step_targets(
                db, step, host_id, merged_targets, target, flow.project_id, base_vars,
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
            pending_tasks.append((execution, step, step_vars, step_host_id))
            started_executions.append(execution)

        await db.commit()

        if not pending_tasks:
            # Every initial step was condition-skipped: nothing will fire a completion
            # event to advance this flow, so finalize now instead of hanging "running".
            print("[FlowManager] No initial steps ran (all skipped). Finalizing flow.")
            await self._finalize_flow_execution(db, flow_exec, "completed")
            return flow_exec, started_executions

        # Spawn background tasks
        for execution, step, step_vars, step_host_id in pending_tasks:
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

        return flow_exec, started_executions

    # ------------------------------------------------------------------
    # Event handler
    # ------------------------------------------------------------------
    async def handle_event(self, event_type: str, data: Any):
        if event_type != "execution_status":
            return
        status = data.get("status")
        execution_id = data.get("execution_id")
        if status not in ("completed", "failed", "timeout", "cancelled"):
            return

        from app.database import async_session_maker
        async with async_session_maker() as db:
            result = await db.execute(
                select(Execution).where(Execution.id == execution_id)
            )
            execution = result.scalar_one_or_none()
            if execution and execution.flow_execution_id:
                print(f"[FlowManager] Execution {execution_id} finished ({status}). Advance flow {execution.flow_execution_id}.")
                await self._schedule_next_steps(db, execution.flow_execution_id, execution)

    # ------------------------------------------------------------------
    # _schedule_next_steps — core flow advancement logic
    # ------------------------------------------------------------------
    async def _schedule_next_steps(self, db: AsyncSession, flow_execution_id: str, last_execution: Execution):
        # Serialize advancement per flow run (C3): concurrent completion events for
        # parallel steps at the same stage must not both schedule the next stage.
        lock = await self._get_flow_lock(flow_execution_id)
        async with lock:
            await self._schedule_next_steps_locked(db, flow_execution_id, last_execution)

    async def _schedule_next_steps_locked(self, db: AsyncSession, flow_execution_id: str, last_execution: Execution):
        # Load FlowExecution record
        fe_result = await db.execute(
            select(FlowExecution).where(FlowExecution.id == flow_execution_id)
        )
        flow_exec = fe_result.scalar_one_or_none()

        if flow_exec and flow_exec.status == "paused":
            print(f"[FlowManager] Flow {flow_execution_id} is paused. Not scheduling next steps.")
            return

        # Load all executions for this flow run
        stmt = (
            select(Execution)
            .where(Execution.flow_execution_id == flow_execution_id)
            .options(selectinload(Execution.checklist_item))
        )
        executions = (await db.execute(stmt)).scalars().all()

        current_index = last_execution.flow_step_index

        # Wait for all tasks at current stage to finish
        current_stage_execs = [e for e in executions if e.flow_step_index == current_index]
        if any(e.status in (ExecutionStatus.RUNNING, ExecutionStatus.PENDING) for e in current_stage_execs):
            print(f"[FlowManager] Stage {current_index} still running. Waiting.")
            return

        if any(e.status == ExecutionStatus.CANCELLED for e in current_stage_execs):
            print(f"[FlowManager] Stage {current_index} cancelled. Halting flow.")
            await self._finalize_flow_execution(db, flow_exec, "cancelled")
            return

        failed_execs = [
            e for e in current_stage_execs
            if e.status in (ExecutionStatus.FAILED, ExecutionStatus.TIMEOUT)
        ]

        # Resolve flow_id from FlowExecution or fallback
        flow_id = flow_exec.flow_id if flow_exec else (last_execution.variables_used or {}).get("flow_id")
        if not flow_id:
            fs_result = await db.execute(
                select(FlowStep.flow_id)
                .where(
                    FlowStep.checklist_item_id == last_execution.item_id,
                    FlowStep.order_index == current_index,
                )
                .limit(1)
            )
            row = fs_result.first()
            flow_id = row[0] if row else None
        if not flow_id:
            print("[FlowManager] Could not determine Flow ID for next step.")
            return

        # Handle failure policy
        if failed_execs:
            step_result = await db.execute(
                select(FlowStep).where(
                    FlowStep.flow_id == flow_id,
                    FlowStep.order_index == current_index,
                )
            )
            step_defs = step_result.scalars().all()
            # Evaluate the policy of the step that ACTUALLY failed (there can be several
            # parallel steps at this stage with different policies), most-severe wins.
            policy_by_item = {sd.checklist_item_id: (sd.on_failure or "stop") for sd in step_defs}
            failed_policies = {policy_by_item.get(e.item_id, "stop") for e in failed_execs}

            if "stop" in failed_policies:
                print(f"[FlowManager] Step {current_index} failed. on_failure=stop. Halting.")
                await self._finalize_flow_execution(db, flow_exec, "failed", error=f"Step {current_index} failed (on_failure=stop)")
                return
            if "skip_remaining" in failed_policies:
                # Distinct from stop: end the flow now but as completed/partial, not failed.
                print(f"[FlowManager] Step {current_index} failed. on_failure=skip_remaining. Ending flow (partial).")
                await self._finalize_flow_execution(db, flow_exec, "completed", error=f"Ended early at step {current_index} (skip_remaining after failure)")
                return
            # all failed steps are "continue" — fall through

        # Build execution outputs context
        execution_outputs: Dict[Any, Any] = {}
        item_name_to_exe: Dict[str, int] = {}
        latest_item_name_to_exe: Dict[str, int] = {}

        for exc in executions:
            if exc.parsed_output:
                execution_outputs[f"exec_{exc.id}"] = exc.parsed_output
                execution_outputs[exc.id] = exc.parsed_output
                if exc.flow_step_index is not None:
                    execution_outputs[f"step_{exc.flow_step_index}"] = exc.parsed_output
                    execution_outputs[exc.flow_step_index] = exc.parsed_output
            if exc.checklist_item:
                item_name_to_exe[exc.checklist_item.name] = exc.id

        # Batch parent: aggregate children parsed_output
        for exc in current_stage_execs:
            if (exc.variables_used or {}).get("is_batch_parent"):
                child_q = select(Execution).where(
                    Execution.variables_used["parent_execution_id"].as_string() == str(exc.id)
                )
                children = (await db.execute(child_q)).scalars().all()
                agg: Dict[str, list] = {}
                for child in children:
                    if child.parsed_output:
                        for k, v in child.parsed_output.items():
                            agg.setdefault(k, []).append(v)
                if agg:
                    key = exc.flow_step_index if exc.flow_step_index is not None else exc.id
                    execution_outputs[f"step_{key}"] = agg
                    execution_outputs[key] = agg

        completed = [
            (e, e.completed_at or e.started_at)
            for e in executions
            if e.checklist_item and (e.completed_at or e.started_at)
        ]
        completed.sort(key=lambda x: (x[1].timestamp() if x[1] else 0), reverse=True)
        for exc, _ in completed:
            name = exc.checklist_item.name
            if name and name not in latest_item_name_to_exe:
                latest_item_name_to_exe[name] = exc.id

        # Re-load project variables (stage-independent — compute once before advancing).
        flow_result = await db.execute(select(Flow).where(Flow.id == flow_id))
        flow = flow_result.scalar_one()

        flow_host_id_for_vars = flow_exec.host_id if flow_exec else last_execution.host_id
        project_vars = await self._load_project_variables(
            db, flow.project_id,
            ad_domain_id=getattr(flow, "ad_domain_id", None),
            host_id=flow_host_id_for_vars,
        )

        base_vars = dict(project_vars)
        valid_indices = [e.flow_step_index for e in executions if e.flow_step_index is not None]
        if valid_indices:
            min_idx = min(valid_indices)
            # C6: merge ALL initial-stage siblings' variables, not just the first one.
            for e in (ex for ex in executions if ex.flow_step_index == min_idx):
                base_vars.update(e.variables_used or {})
        base_vars["flow_id"] = flow_id

        # Re-inject flow-level targets from FlowExecution
        if flow_exec:
            self._inject_targets(base_vars, flow_exec.target, flow_exec.targets or [])

        # Advance through stages, skipping any stage whose steps are ALL condition-
        # skipped (C2) so the flow can't hang "running" on a stage that creates no
        # executions (and therefore never fires a completion event to advance it).
        cursor_index = current_index
        while True:
            result = await db.execute(
                select(FlowStep)
                .where(FlowStep.flow_id == flow_id, FlowStep.order_index > cursor_index)
                .order_by(FlowStep.order_index)
                .options(selectinload(FlowStep.checklist_item))
            )
            all_future_steps = result.scalars().all()

            if not all_future_steps:
                print("[FlowManager] Flow complete.")
                await self._finalize_flow_execution(db, flow_exec, "completed")
                return

            next_index = all_future_steps[0].order_index
            next_steps = [s for s in all_future_steps if s.order_index == next_index]

            # C3: a concurrent completion event may have already scheduled this stage.
            existing_next = await db.execute(
                select(Execution.id).where(
                    Execution.flow_execution_id == flow_execution_id,
                    Execution.flow_step_index == next_index,
                ).limit(1)
            )
            if existing_next.scalar_one_or_none() is not None:
                print(f"[FlowManager] Stage {next_index} already scheduled; skipping (concurrent completion).")
                return

            print(f"[FlowManager] Scheduling Stage {next_index} ({len(next_steps)} steps)")

            scheduled = []
            for step in next_steps:
                step_vars = await self._resolve_step_variables(
                    step, base_vars, execution_outputs,
                    item_name_to_exe, latest_item_name_to_exe,
                )

                if not self._evaluate_condition(
                    step, base_vars, execution_outputs,
                    item_name_to_exe, latest_item_name_to_exe,
                ):
                    print(f"[FlowManager] Skipping step {step.order_index} due to condition.")
                    continue

                flow_host_id = flow_exec.host_id if flow_exec else last_execution.host_id
                flow_targets = flow_exec.targets if flow_exec else []
                flow_target = flow_exec.target if flow_exec else None

                step_host_id, step_target_vars = await self._resolve_step_targets(
                    db, step, flow_host_id, flow_targets, flow_target, flow.project_id, step_vars,
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
                scheduled.append((execution, step, step_vars, step_host_id))

            if scheduled:
                for execution, step, step_vars, step_host_id in scheduled:
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
                return

            # Whole stage was condition-skipped — advance to the following stage.
            print(f"[FlowManager] Stage {next_index} fully skipped; advancing past it.")
            cursor_index = next_index

    # ------------------------------------------------------------------
    # Variable resolution (mirrors orchestrator Global → Domain → Host)
    # ------------------------------------------------------------------
    async def _load_project_variables(
        self,
        db: AsyncSession,
        project_id: Optional[int],
        ad_domain_id: Optional[int] = None,
        host_id: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Load project variables with proper scope layering.

        Resolution order: Global → Domain-scoped → Host-scoped.
        Each layer overrides the previous for the same key.
        """
        if not project_id:
            return {}

        from app.models.variable import ProjectVariable

        # 1. Global variables (no host, no domain)
        global_stmt = select(ProjectVariable).where(
            ProjectVariable.project_id == project_id,
            ProjectVariable.host_id.is_(None),
            ProjectVariable.ad_domain_id.is_(None),
            ProjectVariable.deleted_at.is_(None),
        )
        global_vars = (await db.execute(global_stmt)).scalars().all()

        # 2. Domain-scoped variables
        domain_vars = []
        resolved_domain_id = ad_domain_id

        if not resolved_domain_id and host_id:
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
                row = ad_result.first()
                if row:
                    resolved_domain_id = row[0]

        if resolved_domain_id:
            domain_stmt = select(ProjectVariable).where(
                ProjectVariable.project_id == project_id,
                ProjectVariable.ad_domain_id == resolved_domain_id,
                ProjectVariable.deleted_at.is_(None),
            )
            domain_vars = (await db.execute(domain_stmt)).scalars().all()

        # 3. Host-scoped variables
        host_vars = []
        if host_id:
            host_stmt = select(ProjectVariable).where(
                ProjectVariable.project_id == project_id,
                ProjectVariable.host_id == host_id,
                ProjectVariable.deleted_at.is_(None),
            )
            host_vars = (await db.execute(host_stmt)).scalars().all()

        # 4. Merge: Global → Domain → Host
        merged: Dict[str, Any] = {}
        for var in global_vars:
            merged[var.key] = var.value
        for var in domain_vars:
            merged[var.key] = var.value
        for var in host_vars:
            merged[var.key] = var.value

        return merged

    # ------------------------------------------------------------------
    # Target injection helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _inject_targets(
        variables: Dict[str, Any],
        target: Optional[str],
        targets: List[str],
    ):
        if targets:
            variables["targets"] = targets
            if not target:
                target = targets[0]
        if target:
            variables["target"] = target

    async def _auto_resolve_targets(
        self,
        db: AsyncSession,
        flow: Flow,
    ) -> List[str]:
        """Auto-resolve targets based on flow's domain scope.

        Domain-based flows (ad_domain_id set) get hosts matching that domain.
        Global flows (no ad_domain_id) get all project hosts.
        """
        from app.models.host import Host

        if getattr(flow, "ad_domain_id", None):
            from app.models.ad_domain import ADDomain
            domain_result = await db.execute(
                select(ADDomain.name).where(ADDomain.id == flow.ad_domain_id)
            )
            domain_name = domain_result.scalar_one_or_none()
            if domain_name:
                hosts_result = await db.execute(
                    select(Host.ip_address).where(
                        Host.project_id == flow.project_id,
                        Host.ip_address.isnot(None),
                        Host.deleted_at.is_(None),
                        text("json_extract(hosts.extra_data, '$.domain') = :domain_val"),
                    ).limit(5000),
                    {"domain_val": domain_name},
                )
                return [r[0] for r in hosts_result.all() if r[0]]
            return []

        # Global (unauthenticated) flow — all project hosts
        hosts_result = await db.execute(
            select(Host.ip_address).where(
                Host.project_id == flow.project_id,
                Host.ip_address.isnot(None),
                Host.deleted_at.is_(None),
            ).limit(5000)
        )
        return [r[0] for r in hosts_result.all() if r[0]]

    async def _resolve_step_targets(
        self,
        db: AsyncSession,
        step: FlowStep,
        flow_host_id: Optional[int],
        flow_targets: List[str],
        flow_target: Optional[str],
        project_id: Optional[int],
        step_vars: Dict[str, Any],
        ad_domain_id: Optional[int] = None,
    ) -> Tuple[Optional[int], Dict[str, Any]]:
        """Return (host_id, extra_vars) for the given step based on its target_mode.

        If input_mapping already resolved ``targets`` or ``target`` into step_vars,
        those values take precedence over flow-level injection.
        When ad_domain_id is set, 'all' mode filters hosts by that domain.
        """
        mode = step.target_mode or "inherit"
        extra: Dict[str, Any] = {}

        # If input_mapping already provided targets, don't overwrite
        has_mapped_targets = "targets" in step_vars and step_vars["targets"]
        has_mapped_target = "target" in step_vars and step_vars["target"]
        if has_mapped_targets or has_mapped_target:
            return flow_host_id if mode == "single" else None, extra

        if mode == "single":
            return flow_host_id, extra

        if mode == "inherit":
            self._inject_targets(extra, flow_target, flow_targets)
            return flow_host_id, extra

        if mode == "all" and project_id:
            from app.models.host import Host
            query = select(Host.ip_address).where(
                Host.project_id == project_id,
                Host.ip_address.isnot(None),
                Host.deleted_at.is_(None),
            )
            params = {}
            if ad_domain_id:
                domain_name = await self._get_domain_name(db, ad_domain_id)
                if domain_name:
                    query = query.where(
                        text("json_extract(hosts.extra_data, '$.domain') = :domain_val")
                    )
                    params["domain_val"] = domain_name
            hosts_result = await db.execute(query.limit(5000), params)
            ips = [r[0] for r in hosts_result.all() if r[0]]
            if ips:
                extra["targets"] = ips
                extra["target"] = ips[0]
            return None, extra

        if mode == "filtered" and project_id:
            filter_tags = (step.target_filter or {}).get("tags", [])
            if filter_tags:
                from app.models.host import Host
                placeholders = ", ".join([f":tag_{i}" for i in range(len(filter_tags))])
                tag_filter = text(
                    f"EXISTS (SELECT 1 FROM json_each(hosts.tags) "
                    f"WHERE json_each.value IN ({placeholders}))"
                )
                params = {f"tag_{i}": t for i, t in enumerate(filter_tags)}
                hosts_result = await db.execute(
                    select(Host.ip_address).where(
                        Host.project_id == project_id,
                        Host.ip_address.isnot(None),
                        tag_filter,
                    ).limit(5000),
                    params,
                )
                ips = [r[0] for r in hosts_result.all() if r[0]]
                if ips:
                    extra["targets"] = ips
                    extra["target"] = ips[0]
            return None, extra

        # Fallback: inherit
        self._inject_targets(extra, flow_target, flow_targets)
        return flow_host_id, extra

    @staticmethod
    async def _get_domain_name(db: AsyncSession, ad_domain_id: int) -> Optional[str]:
        from app.models.ad_domain import ADDomain
        result = await db.execute(
            select(ADDomain.name).where(ADDomain.id == ad_domain_id)
        )
        return result.scalar_one_or_none()

    # ------------------------------------------------------------------
    # Variable resolution & condition evaluation
    # ------------------------------------------------------------------
    async def _resolve_step_variables(
        self, step, base_vars, execution_outputs,
        item_name_to_exe, latest_item_name_to_exe=None,
    ):
        step_vars = base_vars.copy()

        for input_name, mapping in (step.input_mapping or {}).items():
            if isinstance(mapping, str):
                source_expr = mapping
                parser_pattern = None
            elif isinstance(mapping, dict):
                source_expr = mapping.get("source", "")
                parser_pattern = mapping.get("parser")
            else:
                continue

            rendered_value = template_engine.render(
                source_expr,
                variables=base_vars,
                execution_outputs=execution_outputs,
                item_name_to_execution=item_name_to_exe,
                latest_item_name_to_execution=latest_item_name_to_exe or item_name_to_exe,
            )

            if parser_pattern and rendered_value:
                from app.core.parser import output_parser
                parsed = await output_parser.parse_output(
                    str(rendered_value), {"result": parser_pattern}
                )
                if "result" in parsed:
                    rendered_value = parsed["result"].value

            step_vars[input_name] = rendered_value

        step_vars["flow_id"] = base_vars.get("flow_id")
        return step_vars

    def _evaluate_condition(
        self, step, base_vars, execution_outputs,
        item_name_to_exe, latest_item_name_to_exe=None,
    ) -> bool:
        if not step.condition:
            return True

        latest_map = latest_item_name_to_exe if latest_item_name_to_exe else item_name_to_exe
        try:
            condition_str = template_engine.render(
                step.condition,
                variables=base_vars,
                execution_outputs=execution_outputs,
                item_name_to_execution=item_name_to_exe,
                latest_item_name_to_execution=latest_map,
            )
            return safe_eval_condition(condition_str)
        except Exception as e:
            # Fail OPEN (run the step) on an evaluation/render error, and log it, rather
            # than silently returning False — a broken condition previously looked like
            # an intentional skip, hiding steps that never ran. A legitimately-false
            # condition returns False normally via safe_eval_condition.
            print(f"[FlowManager] Condition eval error for step "
                  f"{getattr(step, 'order_index', '?')} ({step.condition!r}): {e}. Running step (fail-open).")
            return True

    # ------------------------------------------------------------------
    # Finalize flow execution
    # ------------------------------------------------------------------
    async def _finalize_flow_execution(
        self,
        db: AsyncSession,
        flow_exec: Optional[FlowExecution],
        status: str,
        error: Optional[str] = None,
    ):
        if not flow_exec:
            return
        flow_exec.status = status
        flow_exec.completed_at = datetime.utcnow()
        if error:
            flow_exec.error = error
        await db.commit()

        from app.core.notifications import notification_manager
        await notification_manager.broadcast(
            "flow_completed" if status == "completed" else "flow_failed",
            {
                "flow_execution_id": flow_exec.id,
                "flow_id": flow_exec.flow_id,
                "status": status,
                "error": error,
            },
        )


flow_manager = FlowManager()
