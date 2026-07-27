"""Library Variables API endpoints."""
import json
from typing import List, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.database import get_db
from app.models.library_variable import LibraryVariable
from app.models.project import Project
from app.models.variable import ProjectVariable
from app.core.sync import sync_service

router = APIRouter()


@router.get("/library")
async def list_library_variables(db: AsyncSession = Depends(get_db)):
    """List all library variable templates."""
    result = await db.execute(
        select(LibraryVariable).order_by(LibraryVariable.key)
    )
    variables = result.scalars().all()
    return [
        {
            "id": v.id,
            "key": v.key,
            "value": v.value,
            "var_type": v.var_type,
            "description": v.description,
            "is_default_import": v.is_default_import,
            "created_at": v.created_at.isoformat() if v.created_at else None
        }
        for v in variables
    ]


@router.post("/library", status_code=201)
async def create_library_variable(
    data: dict,
    db: AsyncSession = Depends(get_db)
):
    """Create or update a library variable template."""
    key = data.get("key")
    value = data.get("value", "")
    var_type = data.get("var_type", "string")
    description = data.get("description", "")
    
    if not key:
        raise HTTPException(status_code=400, detail="Key is required")
    
    # Check if exists
    result = await db.execute(
        select(LibraryVariable).where(LibraryVariable.key == key)
    )
    existing = result.scalar_one_or_none()
    
    if existing:
        existing.value = value
        existing.var_type = var_type
        existing.description = description
        variable = existing
    else:
        variable = LibraryVariable(
            key=key,
            value=value,
            var_type=var_type,
            description=description
        )
        db.add(variable)
    
    await db.commit()
    await db.refresh(variable)
    
    return {
        "id": variable.id,
        "key": variable.key,
        "value": variable.value,
        "var_type": variable.var_type,
        "description": variable.description,
        "is_default_import": variable.is_default_import,
    }


@router.put("/library/{variable_id}")
async def update_library_variable(
    variable_id: int,
    data: dict,
    db: AsyncSession = Depends(get_db)
):
    """Update a library variable template."""
    result = await db.execute(
        select(LibraryVariable).where(LibraryVariable.id == variable_id)
    )
    variable = result.scalar_one_or_none()
    
    if not variable:
        raise HTTPException(status_code=404, detail="Variable not found")
    
    if "key" in data:
        variable.key = data["key"]
    if "value" in data:
        variable.value = data["value"]
    if "var_type" in data:
        variable.var_type = data["var_type"]
    if "description" in data:
        variable.description = data["description"]
    if "is_default_import" in data:
        variable.is_default_import = bool(data["is_default_import"])
    
    await db.commit()
    await db.refresh(variable)
    
    return {
        "id": variable.id,
        "key": variable.key,
        "value": variable.value,
        "var_type": variable.var_type,
        "description": variable.description,
        "is_default_import": variable.is_default_import,
    }


@router.delete("/library/{variable_id}", status_code=204)
async def delete_library_variable(
    variable_id: int,
    db: AsyncSession = Depends(get_db)
):
    """Delete a library variable template."""
    result = await db.execute(
        select(LibraryVariable).where(LibraryVariable.id == variable_id)
    )
    variable = result.scalar_one_or_none()
    
    if not variable:
        raise HTTPException(status_code=404, detail="Variable not found")
    
    await db.delete(variable)
    await db.commit()


@router.get("/export/json")
async def export_library_variables_json(
    db: AsyncSession = Depends(get_db),
):
    """Export all library variables as a JSON file."""
    result = await db.execute(
        select(LibraryVariable).order_by(LibraryVariable.key)
    )
    variables = result.scalars().all()

    export_data = {
        "variables": [
            {
                "key": v.key,
                "value": v.value,
                "var_type": v.var_type,
                "description": v.description,
            }
            for v in variables
        ]
    }

    return JSONResponse(
        content=export_data,
        headers={
            "Content-Disposition": (
                f"attachment; filename=variables_export_"
                f"{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
            )
        },
    )


@router.post("/import/file")
async def import_library_variables_file(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
):
    """Import library variables from a JSON file (upsert by key)."""
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

    if "variables" not in data:
        raise HTTPException(
            status_code=400,
            detail='Invalid export file format. Expected a "variables" key.',
        )

    imported_count = 0
    for var_data in data["variables"]:
        key = var_data.get("key")
        if not key:
            continue

        value = var_data.get("value", "")
        var_type = var_data.get("var_type", "string")
        description = var_data.get("description", "")

        result = await db.execute(
            select(LibraryVariable).where(LibraryVariable.key == key)
        )
        existing = result.scalar_one_or_none()

        if existing:
            existing.value = value
            existing.var_type = var_type
            existing.description = description
        else:
            db.add(
                LibraryVariable(
                    key=key,
                    value=value,
                    var_type=var_type,
                    description=description,
                )
            )
        imported_count += 1

    await db.commit()
    return {"status": "imported", "count": imported_count}


async def _project_variable_sync_payload_for_import(
    db: AsyncSession, variable: ProjectVariable
) -> dict:
    """Build sync payload for a project variable (used by import; value always included)."""
    project_public_id = None
    host_public_id = None
    proj_result = await db.execute(select(Project).where(Project.id == variable.project_id))
    project = proj_result.scalar_one_or_none()
    if project:
        project_public_id = project.public_id
    if variable.host_id:
        from app.models.host import Host
        host_result = await db.execute(select(Host).where(Host.id == variable.host_id))
        host = host_result.scalar_one_or_none()
        if host:
            host_public_id = host.public_id
    return {
        "public_id": variable.public_id,
        "project_id": variable.project_id,
        "project_public_id": project_public_id,
        "host_id": variable.host_id,
        "host_public_id": host_public_id,
        "key": variable.key,
        "value": variable.value,
        "var_type": variable.var_type,
        "updated_at": variable.updated_at.isoformat() if variable.updated_at else None,
        "deleted_at": variable.deleted_at.isoformat() if variable.deleted_at else None,
    }


@router.post("/import", status_code=201)
async def import_library_variables(
    project_id: int,
    variable_ids: List[int],
    ad_domain_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db)
):
    """Import library variables into a project, optionally scoped to an AD domain."""
    result = await db.execute(
        select(LibraryVariable).where(LibraryVariable.id.in_(variable_ids))
    )
    library_vars = result.scalars().all()
    
    imported_count = 0
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
        result = await db.execute(lookup)
        existing = result.scalar_one_or_none()
        
        if existing:
            existing.value = lib_var.value
            existing.var_type = lib_var.var_type
            project_var = existing
            operation = "update"
        else:
            project_var = ProjectVariable(
                project_id=project_id,
                key=lib_var.key,
                value=lib_var.value,
                var_type=lib_var.var_type,
                ad_domain_id=ad_domain_id,
            )
            db.add(project_var)
            operation = "create"
        
        await db.flush()
        await db.refresh(project_var)
        payload = await _project_variable_sync_payload_for_import(db, project_var)
        await sync_service.record_event(
            db,
            entity_type="project_variables",
            entity_public_id=project_var.public_id,
            operation=operation,
            payload=payload,
        )
        imported_count += 1
    
    await db.commit()
    return {"status": "imported", "count": imported_count}
