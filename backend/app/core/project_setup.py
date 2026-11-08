"""Auto-import default library templates on project creation and domain addition."""
from typing import List, Optional
from sqlalchemy import select, func as sa_func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.flow import Flow, FlowStep
from app.models.library_variable import LibraryVariable
from app.models.variable import ProjectVariable
from app.models.ad_domain import ADDomain
from app.core.sync import sync_service
from app.core.sync_payloads import (
    checklist_group_sync_payload,
    checklist_item_sync_payload,
    flow_step_sync_payload,
    flow_sync_payload,
    project_variable_sync_payload,
)


async def _get_next_group_order(db: AsyncSession, project_id: int) -> int:
    result = await db.execute(
        select(sa_func.count())
        .select_from(ChecklistGroup)
        .where(ChecklistGroup.project_id == project_id)
    )
    return result.scalar() or 0


async def _clone_checklist_templates(
    db: AsyncSession,
    project_id: int,
    templates: list,
    ad_domain_id: Optional[int],
    start_order: int,
) -> int:
    """Clone checklist group templates into a project. Returns count imported."""
    domain_name = None
    if ad_domain_id:
        domain_result = await db.execute(
            select(ADDomain).where(ADDomain.id == ad_domain_id)
        )
        ad_domain = domain_result.scalar_one_or_none()
        if ad_domain:
            domain_name = ad_domain.name

    imported = 0
    for i, tmpl in enumerate(templates):
        group_name = tmpl.name
        if domain_name:
            group_name = f"{tmpl.name} — {domain_name}"

        new_group = ChecklistGroup(
            project_id=project_id,
            name=group_name,
            description=tmpl.description,
            icon=tmpl.icon,
            color=tmpl.color,
            order_index=start_order + i,
            is_template=False,
            ad_domain_id=ad_domain_id,
            speed_profile=getattr(tmpl, "speed_profile", "default") or "default",
        )
        db.add(new_group)
        await db.flush()

        new_items = []
        for item in tmpl.items:
            new_item = ChecklistItem(
                group_id=new_group.id,
                name=item.name,
                description=item.description,
                command_template=item.command_template,
                output_regex=item.output_regex,
                variables=item.variables,
                input_definitions=getattr(item, "input_definitions", None) or {},
                storage_policy=getattr(item, "storage_policy", None) or {},
                order_index=item.order_index,
                enabled=item.enabled,
                timeout=item.timeout,
                alert_patterns=item.alert_patterns,
                tags=item.tags,
                parameter_schema=getattr(item, "parameter_schema", None) or {},
                target_filter=getattr(item, "target_filter", None) or {},
                finding_template=getattr(item, "finding_template", None) or {},
            )
            db.add(new_item)
            new_items.append(new_item)
        await db.flush()
        # Same events the API import records; without them peers never learn these rows
        # and every later flow step / execution / claim on them defers forever.
        await sync_service.record_event(
            db,
            entity_type="checklist_groups",
            entity_public_id=new_group.public_id,
            operation="create",
            payload=await checklist_group_sync_payload(db, new_group),
        )
        for new_item in new_items:
            await sync_service.record_event(
                db,
                entity_type="checklist_items",
                entity_public_id=new_item.public_id,
                operation="create",
                payload=await checklist_item_sync_payload(db, new_item),
            )
        imported += 1

    return imported


async def _clone_flow_templates(
    db: AsyncSession,
    project_id: int,
    templates: list,
    ad_domain_id: Optional[int],
) -> int:
    """Clone flow templates into a project. Returns count imported."""
    # The project's own cloned items (same domain scope) by name. A step must point at
    # the project's clone, never the library template item: template items are not
    # project-scoped, never sync, and a step referencing one can never apply on a peer.
    project_items_query = (
        select(ChecklistItem)
        .join(ChecklistGroup)
        .where(
            ChecklistGroup.project_id == project_id,
            ChecklistGroup.is_template == False,  # noqa: E712
            ChecklistItem.deleted_at.is_(None),
            ChecklistGroup.ad_domain_id == ad_domain_id
            if ad_domain_id else ChecklistGroup.ad_domain_id.is_(None),
        )
        .order_by(ChecklistGroup.order_index, ChecklistItem.order_index, ChecklistItem.id)
    )
    domain_item_map: dict = {}
    for item in (await db.execute(project_items_query)).scalars().all():
        domain_item_map.setdefault(item.name, item.id)
    project_item_ids = set(domain_item_map.values())

    imported = 0
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

        new_steps = []
        for step in tmpl.steps:
            resolved_item_id = step.checklist_item_id
            if step.checklist_item:
                domain_match = domain_item_map.get(step.checklist_item.name)
                if domain_match:
                    resolved_item_id = domain_match

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
            new_steps.append(new_step)
        await db.flush()
        await sync_service.record_event(
            db,
            entity_type="flows",
            entity_public_id=new_flow.public_id,
            operation="create",
            payload=await flow_sync_payload(db, new_flow),
        )
        for new_step in new_steps:
            if new_step.checklist_item_id not in project_item_ids:
                continue  # still points at a library template item: local-only, can't sync
            await sync_service.record_event(
                db,
                entity_type="flow_steps",
                entity_public_id=new_step.public_id,
                operation="create",
                payload=await flow_step_sync_payload(db, new_step),
            )
        imported += 1

    return imported


async def _clone_default_variables(
    db: AsyncSession,
    project_id: int,
    ad_domain_id: Optional[int],
) -> int:
    """Import default library variables into a project."""
    result = await db.execute(
        select(LibraryVariable).where(LibraryVariable.is_default_import == True)
    )
    library_vars = result.scalars().all()

    imported = 0
    for lib_var in library_vars:
        lookup = (
            select(ProjectVariable)
            .where(ProjectVariable.project_id == project_id)
            .where(ProjectVariable.key == lib_var.key)
            .where(ProjectVariable.host_id.is_(None))
            .where(ProjectVariable.deleted_at.is_(None))
        )
        if ad_domain_id is not None:
            lookup = lookup.where(ProjectVariable.ad_domain_id == ad_domain_id)
        else:
            lookup = lookup.where(ProjectVariable.ad_domain_id.is_(None))
        existing = (await db.execute(lookup)).scalars().first()

        if not existing:
            new_var = ProjectVariable(
                project_id=project_id,
                key=lib_var.key,
                value=lib_var.value,
                var_type=lib_var.var_type,
                ad_domain_id=ad_domain_id,
            )
            db.add(new_var)
            await db.flush()
            await sync_service.record_event(
                db,
                entity_type="project_variables",
                entity_public_id=new_var.public_id,
                operation="create",
                payload=await project_variable_sync_payload(db, new_var),
            )
            imported += 1

    return imported


async def auto_import_for_project(
    db: AsyncSession,
    project_id: int,
    domain_ids: List[int],
) -> dict:
    """Auto-import default templates on project creation.

    1. Global checklists (requires_auth=False, is_default_import=True)
    2. Per-domain checklists (requires_auth=True, is_default_import=True)
    3. Same for flows
    4. Default library variables (global + per-domain)

    Does NOT commit — caller must commit.
    """
    counts = {"checklists": 0, "flows": 0, "variables": 0}

    # --- Global (no-auth) checklists ---
    global_cl_result = await db.execute(
        select(ChecklistGroup)
        .where(
            ChecklistGroup.project_id.is_(None),
            ChecklistGroup.is_template == True,
            ChecklistGroup.is_default_import == True,
            ChecklistGroup.requires_auth == False,
        )
        .options(selectinload(ChecklistGroup.items))
    )
    global_checklists = global_cl_result.scalars().all()

    order = await _get_next_group_order(db, project_id)
    if global_checklists:
        n = await _clone_checklist_templates(db, project_id, global_checklists, None, order)
        counts["checklists"] += n
        order += n

    # --- Domain-based (auth) checklists ---
    auth_cl_result = await db.execute(
        select(ChecklistGroup)
        .where(
            ChecklistGroup.project_id.is_(None),
            ChecklistGroup.is_template == True,
            ChecklistGroup.is_default_import == True,
            ChecklistGroup.requires_auth == True,
        )
        .options(selectinload(ChecklistGroup.items))
    )
    auth_checklists = auth_cl_result.scalars().all()

    for domain_id in domain_ids:
        if auth_checklists:
            n = await _clone_checklist_templates(db, project_id, auth_checklists, domain_id, order)
            counts["checklists"] += n
            order += n

    # --- Global (no-auth) flows ---
    global_fl_result = await db.execute(
        select(Flow)
        .where(
            Flow.project_id.is_(None),
            Flow.is_template == True,
            Flow.is_default_import == True,
            Flow.requires_auth == False,
        )
        .options(selectinload(Flow.steps).selectinload(FlowStep.checklist_item))
    )
    global_flows = global_fl_result.scalars().all()
    if global_flows:
        n = await _clone_flow_templates(db, project_id, global_flows, None)
        counts["flows"] += n

    # --- Domain-based (auth) flows ---
    auth_fl_result = await db.execute(
        select(Flow)
        .where(
            Flow.project_id.is_(None),
            Flow.is_template == True,
            Flow.is_default_import == True,
            Flow.requires_auth == True,
        )
        .options(selectinload(Flow.steps).selectinload(FlowStep.checklist_item))
    )
    auth_flows = auth_fl_result.scalars().all()
    for domain_id in domain_ids:
        if auth_flows:
            n = await _clone_flow_templates(db, project_id, auth_flows, domain_id)
            counts["flows"] += n

    # --- Variables (global) ---
    counts["variables"] += await _clone_default_variables(db, project_id, None)

    # --- Variables (per-domain) ---
    for domain_id in domain_ids:
        counts["variables"] += await _clone_default_variables(db, project_id, domain_id)

    return counts


async def auto_import_for_domain(
    db: AsyncSession,
    project_id: int,
    domain_id: int,
) -> dict:
    """Auto-import domain-based (requires_auth) defaults when a new domain is added.

    Does NOT commit — caller must commit.
    """
    counts = {"checklists": 0, "flows": 0, "variables": 0}

    # Auth checklists
    auth_cl_result = await db.execute(
        select(ChecklistGroup)
        .where(
            ChecklistGroup.project_id.is_(None),
            ChecklistGroup.is_template == True,
            ChecklistGroup.is_default_import == True,
            ChecklistGroup.requires_auth == True,
        )
        .options(selectinload(ChecklistGroup.items))
    )
    auth_checklists = auth_cl_result.scalars().all()
    if auth_checklists:
        order = await _get_next_group_order(db, project_id)
        counts["checklists"] = await _clone_checklist_templates(
            db, project_id, auth_checklists, domain_id, order
        )

    # Auth flows
    auth_fl_result = await db.execute(
        select(Flow)
        .where(
            Flow.project_id.is_(None),
            Flow.is_template == True,
            Flow.is_default_import == True,
            Flow.requires_auth == True,
        )
        .options(selectinload(Flow.steps).selectinload(FlowStep.checklist_item))
    )
    auth_flows = auth_fl_result.scalars().all()
    if auth_flows:
        counts["flows"] = await _clone_flow_templates(
            db, project_id, auth_flows, domain_id
        )

    # Variables (per-domain)
    counts["variables"] = await _clone_default_variables(db, project_id, domain_id)

    return counts
