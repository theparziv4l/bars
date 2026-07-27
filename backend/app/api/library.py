"""Whole-library export/import API endpoints."""
import json
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.flow import Flow, FlowStep
from app.models.library_variable import LibraryVariable

router = APIRouter()


def _serialize_checklist_groups(groups) -> list:
    """Serialize checklist groups + items to export-compatible dicts."""
    result = []
    for group in groups:
        group_data = {
            "name": group.name,
            "description": group.description,
            "is_default_import": group.is_default_import,
            "requires_auth": group.requires_auth,
            "speed_profile": group.speed_profile,
            "items": [],
        }
        for item in group.items:
            group_data["items"].append({
                "name": item.name,
                "description": item.description,
                "command_template": item.command_template,
                "output_regex": item.output_regex,
                "input_definitions": item.input_definitions,
                "storage_policy": item.storage_policy,
                "parameter_schema": item.parameter_schema,
                "timeout": item.timeout,
                "alert_patterns": item.alert_patterns,
                "finding_template": item.finding_template,
                "tags": item.tags,
                "enabled": item.enabled,
            })
        result.append(group_data)
    return result


def _serialize_flows(flows) -> list:
    """Serialize flows + steps to export-compatible dicts."""
    result = []
    for flow in flows:
        flow_data = {
            "name": flow.name,
            "description": flow.description,
            "is_default_import": flow.is_default_import,
            "requires_auth": flow.requires_auth,
            "tags": flow.tags,
            "flow_definition": flow.flow_definition,
            "steps": [],
        }
        for step in flow.steps:
            item = step.checklist_item
            flow_data["steps"].append({
                "checklist_item_name": item.name if item else None,
                "order_index": step.order_index,
                "input_mapping": step.input_mapping,
                "condition": step.condition,
                "on_failure": step.on_failure,
                "timeout_override": step.timeout_override,
                "ui_position": step.ui_position,
            })
        result.append(flow_data)
    return result


def _serialize_variables(variables) -> list:
    """Serialize library variables to export-compatible dicts."""
    return [
        {
            "key": v.key,
            "value": v.value,
            "var_type": v.var_type,
            "description": v.description,
            "is_default_import": v.is_default_import,
        }
        for v in variables
    ]


@router.get("/export")
async def export_whole_library(db: AsyncSession = Depends(get_db)):
    """Export the entire library (checklists, flows, variables) as a single JSON file."""
    groups_result = await db.execute(
        select(ChecklistGroup)
        .options(selectinload(ChecklistGroup.items))
        .where(ChecklistGroup.project_id.is_(None))
    )
    groups = groups_result.scalars().all()

    flows_result = await db.execute(
        select(Flow)
        .options(selectinload(Flow.steps).selectinload(FlowStep.checklist_item))
        .where(Flow.project_id.is_(None))
    )
    flows = flows_result.scalars().all()

    vars_result = await db.execute(
        select(LibraryVariable).order_by(LibraryVariable.key)
    )
    variables = vars_result.scalars().all()

    export_data = {
        "checklists": {"groups": _serialize_checklist_groups(groups)},
        "flows": {"flows": _serialize_flows(flows)},
        "variables": {"variables": _serialize_variables(variables)},
    }

    return JSONResponse(
        content=export_data,
        headers={
            "Content-Disposition": (
                f"attachment; filename=library_export_"
                f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
            )
        },
    )


@router.post("/import")
async def import_whole_library(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Import a whole-library JSON file. Accepts any combination of checklists, flows, and variables."""
    if not file.filename.endswith(".json"):
        raise HTTPException(
            status_code=400,
            detail="Invalid file format. Please upload a JSON file.",
        )

    try:
        content = await file.read()
        data = json.loads(content)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON content.")

    counts = {"checklists": 0, "flows": 0, "variables": 0}
    warnings: list[str] = []

    # --- Checklists ---
    checklists_data = data.get("checklists", {})
    groups_list = checklists_data.get("groups", [])
    if groups_list:
        from sqlalchemy import func as sa_func

        current_count_q = (
            select(sa_func.count())
            .select_from(ChecklistGroup)
            .where(ChecklistGroup.project_id.is_(None))
        )
        current_count = (await db.execute(current_count_q)).scalar() or 0

        for i, group_data in enumerate(groups_list):
            new_group = ChecklistGroup(
                project_id=None,
                name=group_data["name"],
                description=group_data.get("description"),
                icon=group_data.get("icon"),
                color=group_data.get("color"),
                order_index=current_count + i,
                is_template=True,
                collapsed=group_data.get("collapsed", False),
                is_default_import=group_data.get("is_default_import", False),
                requires_auth=group_data.get("requires_auth", False),
                speed_profile=group_data.get("speed_profile", "default"),
            )
            db.add(new_group)
            await db.flush()

            for j, item_data in enumerate(group_data.get("items", [])):
                db.add(
                    ChecklistItem(
                        group_id=new_group.id,
                        name=item_data["name"],
                        description=item_data.get("description"),
                        command_template=item_data.get("command_template", ""),
                        output_regex=item_data.get("output_regex") or {},
                        variables=item_data.get("variables") or {},
                        input_definitions=item_data.get("input_definitions") or {},
                        storage_policy=item_data.get("storage_policy") or {},
                        parameter_schema=item_data.get("parameter_schema") or {},
                        target_filter=item_data.get("target_filter") or {},
                        timeout=item_data.get("timeout", 3600),
                        alert_patterns=item_data.get("alert_patterns") or {},
                        finding_template=item_data.get("finding_template") or {},
                        tags=item_data.get("tags") or [],
                        enabled=item_data.get("enabled", True),
                        order_index=j,
                    )
                )

            counts["checklists"] += 1

        await db.flush()

    # --- Flows ---
    flows_data = data.get("flows", {})
    flows_list = flows_data.get("flows", [])
    if flows_list:
        # Build item-name -> id map from global template items
        items_result = await db.execute(
            select(ChecklistItem)
            .join(ChecklistGroup)
            .where(ChecklistGroup.project_id.is_(None))
        )
        item_name_map = {item.name: item.id for item in items_result.scalars().all()}

        for flow_data in flows_list:
            new_flow = Flow(
                project_id=None,
                name=flow_data["name"],
                description=flow_data.get("description"),
                is_template=True,
                is_default_import=flow_data.get("is_default_import", False),
                requires_auth=flow_data.get("requires_auth", False),
                tags=flow_data.get("tags", []),
                flow_definition=flow_data.get("flow_definition", {}),
            )
            db.add(new_flow)
            await db.flush()

            for si, step_data in enumerate(flow_data.get("steps", [])):
                item_name = step_data.get("checklist_item_name")
                target_item_id = None

                if item_name and item_name in item_name_map:
                    target_item_id = item_name_map[item_name]
                elif item_name:
                    fallback = await db.execute(
                        select(ChecklistItem)
                        .where(ChecklistItem.name == item_name)
                        .limit(1)
                    )
                    fb_item = fallback.scalar_one_or_none()
                    if fb_item:
                        target_item_id = fb_item.id

                if not target_item_id:
                    warnings.append(
                        f"Flow '{new_flow.name}': Step {si + 1} skipped – "
                        f"item '{item_name}' not found."
                    )
                    continue

                db.add(
                    FlowStep(
                        flow_id=new_flow.id,
                        checklist_item_id=target_item_id,
                        order_index=step_data.get("order_index", si),
                        input_mapping=step_data.get("input_mapping") or {},
                        condition=step_data.get("condition"),
                        on_failure=step_data.get("on_failure", "stop"),
                        timeout_override=step_data.get("timeout_override"),
                        ui_position=step_data.get("ui_position", {}),
                    )
                )

            counts["flows"] += 1

    # --- Variables ---
    variables_data = data.get("variables", {})
    variables_list = variables_data.get("variables", [])
    for var_data in variables_list:
        key = var_data.get("key")
        if not key:
            continue

        result = await db.execute(
            select(LibraryVariable).where(LibraryVariable.key == key)
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.value = var_data.get("value", "")
            existing.var_type = var_data.get("var_type", "string")
            existing.description = var_data.get("description", "")
            existing.is_default_import = var_data.get("is_default_import", existing.is_default_import)
        else:
            db.add(
                LibraryVariable(
                    key=key,
                    value=var_data.get("value", ""),
                    var_type=var_data.get("var_type", "string"),
                    description=var_data.get("description", ""),
                    is_default_import=var_data.get("is_default_import", False),
                )
            )
        counts["variables"] += 1

    await db.commit()

    response = {"status": "imported", "counts": counts}
    if warnings:
        response["warnings"] = warnings
    return response
