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

        for item in tmpl.items:
            db.add(ChecklistItem(
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
            ))
        await db.flush()
        imported += 1

    return imported


async def _clone_flow_templates(
    db: AsyncSession,
    project_id: int,
    templates: list,
    ad_domain_id: Optional[int],
) -> int:
    """Clone flow templates into a project. Returns count imported."""
    domain_item_map = {}
    if ad_domain_id:
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

        for step in tmpl.steps:
            resolved_item_id = step.checklist_item_id
            if ad_domain_id and step.checklist_item:
                domain_match = domain_item_map.get(step.checklist_item.name)
                if domain_match:
                    resolved_item_id = domain_match

            db.add(FlowStep(
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
            ))
        await db.flush()
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
        )
        if ad_domain_id is not None:
            lookup = lookup.where(ProjectVariable.ad_domain_id == ad_domain_id)
        else:
            lookup = lookup.where(ProjectVariable.ad_domain_id.is_(None))
        existing = (await db.execute(lookup)).scalar_one_or_none()

        if not existing:
            db.add(ProjectVariable(
                project_id=project_id,
                key=lib_var.key,
                value=lib_var.value,
                var_type=lib_var.var_type,
                ad_domain_id=ad_domain_id,
            ))
            await db.flush()
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
