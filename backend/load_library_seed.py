#!/usr/bin/env python3
"""Load a library export into the database, replacing all existing library templates.

Usage:
    python load_library_seed.py                      # backend/library_seed.json
    python load_library_seed.py /path/to/export.json # any /api/v1/library/export file
"""
import asyncio
import json
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from app.database import async_session_maker, init_db
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.flow import Flow, FlowStep
from app.models.library_variable import LibraryVariable

from sqlalchemy import select, delete
from sqlalchemy.orm import selectinload


DEFAULT_SEED_PATH = os.path.join(os.path.dirname(__file__), "library_seed.json")
# Any /api/v1/library/export payload works here, e.g. the repo's suggested-library.json.
SEED_PATH = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SEED_PATH


async def clear_library(session):
    """Delete all global template groups, flows, and library variables."""
    await session.execute(
        delete(ChecklistGroup).where(ChecklistGroup.project_id.is_(None))
    )
    await session.execute(
        delete(Flow).where(Flow.project_id.is_(None))
    )
    await session.execute(delete(LibraryVariable))
    await session.flush()


async def load_seed(session, data):
    """Import seed data into DB."""
    counts = {"checklists": 0, "flows": 0, "variables": 0, "items": 0}

    groups_list = data.get("checklists", {}).get("groups", [])
    for i, group_data in enumerate(groups_list):
        new_group = ChecklistGroup(
            project_id=None,
            name=group_data["name"],
            description=group_data.get("description"),
            icon=group_data.get("icon"),
            color=group_data.get("color"),
            order_index=i,
            is_template=True,
            collapsed=False,
            is_default_import=group_data.get("is_default_import", False),
            requires_auth=group_data.get("requires_auth", False),
            speed_profile=group_data.get("speed_profile", "default"),
        )
        session.add(new_group)
        await session.flush()

        for j, item_data in enumerate(group_data.get("items", [])):
            session.add(ChecklistItem(
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
            ))
            counts["items"] += 1

        counts["checklists"] += 1

    await session.flush()

    item_name_map = {}
    items_result = await session.execute(
        select(ChecklistItem)
        .join(ChecklistGroup)
        .where(ChecklistGroup.project_id.is_(None))
    )
    for item in items_result.scalars().all():
        item_name_map[item.name] = item.id

    flows_list = data.get("flows", {}).get("flows", [])
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
        session.add(new_flow)
        await session.flush()

        skipped = 0
        for si, step_data in enumerate(flow_data.get("steps", [])):
            item_name = step_data.get("checklist_item_name")
            target_item_id = item_name_map.get(item_name) if item_name else None

            if not target_item_id:
                skipped += 1
                continue

            session.add(FlowStep(
                flow_id=new_flow.id,
                checklist_item_id=target_item_id,
                order_index=step_data.get("order_index", si),
                input_mapping=step_data.get("input_mapping") or {},
                condition=step_data.get("condition"),
                on_failure=step_data.get("on_failure", "continue"),
                timeout_override=step_data.get("timeout_override"),
                ui_position=step_data.get("ui_position", {}),
            ))

        if skipped:
            print(f"  Flow '{new_flow.name}': {skipped} steps skipped (item not found)")
        counts["flows"] += 1

    variables_list = data.get("variables", {}).get("variables", [])
    for var_data in variables_list:
        key = var_data.get("key")
        if not key:
            continue
        session.add(LibraryVariable(
            key=key,
            value=var_data.get("value", ""),
            var_type=var_data.get("var_type", "string"),
            description=var_data.get("description", ""),
            is_default_import=var_data.get("is_default_import", False),
        ))
        counts["variables"] += 1

    await session.commit()
    return counts


async def main():
    await init_db()

    if not os.path.exists(SEED_PATH):
        sys.exit(f"No such library file: {SEED_PATH}")

    print(f"Reading {SEED_PATH}")
    with open(SEED_PATH, "r") as f:
        data = json.load(f)

    async with async_session_maker() as session:
        print("Clearing existing library templates...")
        await clear_library(session)
        await session.commit()

        print("Loading library seed...")
        counts = await load_seed(session, data)
        print(f"Done! Imported {counts['checklists']} groups, {counts['items']} items, {counts['flows']} flows, {counts['variables']} variables")


if __name__ == "__main__":
    asyncio.run(main())
