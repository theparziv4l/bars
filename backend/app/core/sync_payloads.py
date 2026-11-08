"""Sync payload builders (extracted from the sync engine module).

Pure read-side builders: given a DB session and a row, they return the
JSON-serializable dict shipped inside a SyncEvent. ``app.core.sync``
re-exports every name below for backward compatibility.
"""

from __future__ import annotations

from typing import Any, Dict

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.ad_domain import ADDomain
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.checklist_claim import ChecklistClaim
from app.models.execution import Execution
from app.models.file import DiscoveredFile
from app.models.flow import Flow, FlowStep
from app.models.host import Host, Service
from app.models.project import Project
from app.models.user import User
from app.models.variable import ProjectVariable


async def _project_public_id_for_item(db: AsyncSession, item_id) -> str | None:
    """item -> group -> project public id (None for template/unscoped items)."""
    if not item_id:
        return None
    return (await db.execute(
        select(Project.public_id)
        .join(ChecklistGroup, ChecklistGroup.project_id == Project.id)
        .join(ChecklistItem, ChecklistItem.group_id == ChecklistGroup.id)
        .where(ChecklistItem.id == item_id)
    )).scalar_one_or_none()


async def _project_public_id_for_host(db: AsyncSession, host_id) -> str | None:
    if not host_id:
        return None
    return (await db.execute(
        select(Project.public_id)
        .join(Host, Host.project_id == Project.id)
        .where(Host.id == host_id)
    )).scalar_one_or_none()


async def execution_sync_payload(db: AsyncSession, execution: Execution) -> Dict[str, Any]:
    """Build full sync payload for an execution (create/update)."""
    item_public_id = None
    if execution.item_id:
        item_result = await db.execute(
            select(ChecklistItem).where(ChecklistItem.id == execution.item_id)
        )
        item = item_result.scalar_one_or_none()
        item_public_id = item.public_id if item else None
    host_public_id = None
    if execution.host_id:
        host_result = await db.execute(select(Host).where(Host.id == execution.host_id))
        host = host_result.scalar_one_or_none()
        host_public_id = host.public_id if host else None
    return {
        "public_id": execution.public_id,
        # Project-scoped sync filters on this key; without it executions never
        # reach a peer that syncs a single project.
        "project_public_id": await _project_public_id_for_item(db, execution.item_id),
        "item_id": execution.item_id,
        "item_public_id": item_public_id,
        "host_id": execution.host_id,
        "host_public_id": host_public_id,
        "status": execution.status.value if execution.status else "pending",
        "command": execution.command or "",
        "variables_used": execution.variables_used or {},
        "version": getattr(execution, "version", 1) or 1,
        "stdout": getattr(execution, "stdout", None),
        "stderr": getattr(execution, "stderr", None),
        "exit_code": getattr(execution, "exit_code", None),
        "started_at": execution.started_at.isoformat() if getattr(execution, "started_at", None) else None,
        "completed_at": execution.completed_at.isoformat() if getattr(execution, "completed_at", None) else None,
        "started_by_user_id": getattr(execution, "started_by_user_id", None),
        "updated_at": execution.updated_at.isoformat() if execution.updated_at else None,
        "deleted_at": execution.deleted_at.isoformat() if getattr(execution, "deleted_at", None) else None,
    }


async def host_sync_payload(db: AsyncSession, host: Host) -> Dict[str, Any]:
    """Build sync payload for a host (for use by API and orchestrator)."""
    project_public_id = None
    if host.project_id:
        proj_result = await db.execute(select(Project).where(Project.id == host.project_id))
        project = proj_result.scalar_one_or_none()
        if project:
            project_public_id = project.public_id
    return {
        "public_id": host.public_id,
        "project_id": host.project_id,
        "project_public_id": project_public_id,
        "ip_address": host.ip_address,
        "hostname": host.hostname,
        "fqdn": host.fqdn,
        "os_info": host.os_info,
        "status": host.status,
        "notes": host.notes,
        "extra_data": host.extra_data or {},
        "tags": host.tags or [],
        "updated_at": host.updated_at.isoformat() if host.updated_at else None,
        "deleted_at": host.deleted_at.isoformat() if host.deleted_at else None,
    }


async def finding_sync_payload(db: AsyncSession, finding) -> Dict[str, Any]:
    """Build sync payload for a finding (create/update)."""
    project_public_id = None
    if finding.project_id:
        proj = (await db.execute(select(Project).where(Project.id == finding.project_id))).scalar_one_or_none()
        project_public_id = proj.public_id if proj else None
    host_public_id = None
    if finding.host_id:
        host = (await db.execute(select(Host).where(Host.id == finding.host_id))).scalar_one_or_none()
        host_public_id = host.public_id if host else None
    item_public_id = None
    if finding.item_id:
        item = (await db.execute(
            select(ChecklistItem).where(ChecklistItem.id == finding.item_id)
        )).scalar_one_or_none()
        item_public_id = item.public_id if item else None
    execution_public_id = None
    if finding.execution_id:
        ex = (await db.execute(
            select(Execution).where(Execution.id == finding.execution_id)
        )).scalar_one_or_none()
        execution_public_id = ex.public_id if ex else None
    return {
        "public_id": finding.public_id,
        "project_public_id": project_public_id,
        "host_public_id": host_public_id,
        "item_public_id": item_public_id,
        "execution_public_id": execution_public_id,
        "title": finding.title,
        "description": finding.description,
        "severity": finding.severity.value if finding.severity else "info",
        "status": finding.status.value if finding.status else "open",
        "cvss_vector": finding.cvss_vector,
        "cvss_score": finding.cvss_score,
        "cwe": finding.cwe,
        "cve": finding.cve,
        "remediation": finding.remediation,
        "notes": finding.notes,
        "references": finding.references or [],
        "source": finding.source,
        "evidence_text": finding.evidence_text,
        "occurrences": finding.occurrences,
        "discovered_at": finding.discovered_at.isoformat() if finding.discovered_at else None,
        "updated_at": finding.updated_at.isoformat() if finding.updated_at else None,
        "deleted_at": finding.deleted_at.isoformat() if finding.deleted_at else None,
    }


async def service_sync_payload(db: AsyncSession, service: Service) -> Dict[str, Any]:
    """Build sync payload for a service."""
    host_public_id = None
    if service.host_id:
        host_result = await db.execute(select(Host).where(Host.id == service.host_id))
        host = host_result.scalar_one_or_none()
        if host:
            host_public_id = host.public_id
    return {
        "public_id": service.public_id,
        "project_public_id": await _project_public_id_for_host(db, service.host_id),
        "host_id": service.host_id,
        "host_public_id": host_public_id,
        "port": service.port,
        "protocol": service.protocol or "tcp",
        "name": service.name,
        "version": service.version,
        "product": service.product,
        "state": service.state,
        "banner": service.banner,
        "ssl": getattr(service, "ssl", False),
        "extra_data": service.extra_data or {},
        "discovered_at": service.discovered_at.isoformat() if service.discovered_at else None,
    }


async def discovered_file_sync_payload(db: AsyncSession, discovered_file: DiscoveredFile) -> Dict[str, Any]:
    """Build sync payload for a discovered file/share."""
    host_public_id = None
    execution_public_id = None
    if discovered_file.host_id:
        host_result = await db.execute(select(Host).where(Host.id == discovered_file.host_id))
        h = host_result.scalar_one_or_none()
        if h:
            host_public_id = h.public_id
    if discovered_file.execution_id:
        exec_result = await db.execute(
            select(Execution).where(Execution.id == discovered_file.execution_id)
        )
        ex = exec_result.scalar_one_or_none()
        if ex:
            execution_public_id = ex.public_id
    return {
        "public_id": discovered_file.public_id,
        "project_public_id": await _project_public_id_for_host(db, discovered_file.host_id),
        "host_id": discovered_file.host_id,
        "host_public_id": host_public_id,
        "execution_id": discovered_file.execution_id,
        "execution_public_id": execution_public_id,
        "share_name": discovered_file.share_name,
        "path": discovered_file.path,
        "name": discovered_file.name,
        "file_type": discovered_file.file_type,
        "size": discovered_file.size,
        "permissions": discovered_file.permissions or {},
        "is_readable": discovered_file.is_readable,
        "is_writable": discovered_file.is_writable,
        "is_executable": getattr(discovered_file, "is_executable", False),
        "is_interesting": discovered_file.is_interesting,
        "interest_reason": discovered_file.interest_reason,
        "content_preview": discovered_file.content_preview,
        "extra_data": discovered_file.extra_data or {},
        "discovered_at": discovered_file.discovered_at.isoformat() if discovered_file.discovered_at else None,
    }


async def flow_sync_payload(db: AsyncSession, flow: Flow) -> Dict[str, Any]:
    """Build sync payload for a flow."""
    project_public_id = None
    if flow.project_id:
        proj_result = await db.execute(select(Project).where(Project.id == flow.project_id))
        project = proj_result.scalar_one_or_none()
        if project:
            project_public_id = project.public_id
    return {
        "public_id": flow.public_id,
        "project_id": flow.project_id,
        "project_public_id": project_public_id,
        "name": flow.name,
        "description": flow.description,
        "is_template": flow.is_template,
        "flow_definition": flow.flow_definition or {},
        "tags": flow.tags or [],
        "created_at": flow.created_at.isoformat() if flow.created_at else None,
        "updated_at": flow.updated_at.isoformat() if flow.updated_at else None,
    }


async def project_variable_sync_payload(
    db: AsyncSession, variable: ProjectVariable
) -> Dict[str, Any]:
    """Build sync payload for a project variable (for API and orchestrator)."""
    project_public_id = None
    host_public_id = None
    ad_domain_public_id = None
    if variable.ad_domain_id:
        ad_domain_public_id = (await db.execute(
            select(ADDomain.public_id).where(ADDomain.id == variable.ad_domain_id)
        )).scalar_one_or_none()
    if variable.project_id:
        proj_result = await db.execute(
            select(Project).where(Project.id == variable.project_id)
        )
        project = proj_result.scalar_one_or_none()
        if project:
            project_public_id = project.public_id
    if variable.host_id:
        host_result = await db.execute(
            select(Host).where(Host.id == variable.host_id)
        )
        host = host_result.scalar_one_or_none()
        if host:
            host_public_id = host.public_id
    return {
        "public_id": variable.public_id,
        "project_id": variable.project_id,
        "project_public_id": project_public_id,
        "host_id": variable.host_id,
        "host_public_id": host_public_id,
        "ad_domain_public_id": ad_domain_public_id,
        "key": variable.key,
        "value": variable.value,
        "var_type": variable.var_type or "string",
        "updated_at": variable.updated_at.isoformat() if variable.updated_at else None,
        "deleted_at": variable.deleted_at.isoformat() if variable.deleted_at else None,
    }


async def claim_sync_payload(db: AsyncSession, claim: ChecklistClaim) -> Dict[str, Any]:
    """Build sync payload for a checklist claim."""
    project_public_id = None
    item_public_id = None
    host_public_id = None
    user_public_id = None
    if claim.project_id:
        proj_result = await db.execute(
            select(Project).where(Project.id == claim.project_id)
        )
        project = proj_result.scalar_one_or_none()
        if project:
            project_public_id = project.public_id
    if claim.item_id:
        item_result = await db.execute(
            select(ChecklistItem).where(ChecklistItem.id == claim.item_id)
        )
        item = item_result.scalar_one_or_none()
        if item:
            item_public_id = item.public_id
    if claim.host_id is not None:
        host_result = await db.execute(
            select(Host).where(Host.id == claim.host_id)
        )
        host = host_result.scalar_one_or_none()
        if host:
            host_public_id = host.public_id
    if claim.claimed_by_user_id is not None:
        user_result = await db.execute(
            select(User).where(User.id == claim.claimed_by_user_id)
        )
        c_user = user_result.scalar_one_or_none()
        if c_user:
            user_public_id = c_user.public_id
    return {
        "public_id": claim.public_id,
        "project_id": claim.project_id,
        "project_public_id": project_public_id,
        "item_id": claim.item_id,
        "item_public_id": item_public_id,
        "host_id": claim.host_id,
        "host_public_id": host_public_id,
        "host_scope_key": claim.host_scope_key,
        "claimed_by_user_id": claim.claimed_by_user_id,
        "claimed_by_user_public_id": user_public_id,
        "claimed_at": claim.claimed_at.isoformat() if claim.claimed_at else None,
        "lease_expires_at": claim.lease_expires_at.isoformat() if claim.lease_expires_at else None,
        "is_active": claim.is_active,
        "version": getattr(claim, "version", 1),
        "updated_at": (claim.updated_at.isoformat() if claim.updated_at else None)
        if getattr(claim, "updated_at", None) else None,
        "deleted_at": (claim.deleted_at.isoformat() if claim.deleted_at else None)
        if getattr(claim, "deleted_at", None) else None,
    }


async def flow_step_sync_payload(db: AsyncSession, step: FlowStep) -> Dict[str, Any]:
    """Build sync payload for a flow step."""
    flow_public_id = None
    item_public_id = None
    if step.flow_id:
        flow_result = await db.execute(select(Flow).where(Flow.id == step.flow_id))
        f = flow_result.scalar_one_or_none()
        if f:
            flow_public_id = f.public_id
    if step.checklist_item_id:
        item_result = await db.execute(
            select(ChecklistItem).where(ChecklistItem.id == step.checklist_item_id)
        )
        item = item_result.scalar_one_or_none()
        if item:
            item_public_id = item.public_id
    project_public_id = None
    if step.flow_id:
        project_public_id = (await db.execute(
            select(Project.public_id)
            .join(Flow, Flow.project_id == Project.id)
            .where(Flow.id == step.flow_id)
        )).scalar_one_or_none()
    return {
        "public_id": step.public_id,
        "project_public_id": project_public_id,
        "flow_id": step.flow_id,
        "flow_public_id": flow_public_id,
        "checklist_item_id": step.checklist_item_id,
        "item_public_id": item_public_id,
        "order_index": step.order_index,
        "input_mapping": step.input_mapping or {},
        "condition": step.condition,
        "on_failure": step.on_failure or "stop",
        "timeout_override": step.timeout_override,
        "ui_position": step.ui_position or {},
    }


async def ad_domain_sync_payload(db: AsyncSession, domain: ADDomain) -> Dict[str, Any]:
    """Build sync payload for an AD domain."""
    project_public_id = (await db.execute(
        select(Project.public_id).where(Project.id == domain.project_id)
    )).scalar_one_or_none()
    parent_public_id = None
    if domain.parent_domain_id:
        parent_public_id = (await db.execute(
            select(ADDomain.public_id).where(ADDomain.id == domain.parent_domain_id)
        )).scalar_one_or_none()
    return {
        "public_id": domain.public_id,
        "project_id": domain.project_id,
        "project_public_id": project_public_id,
        "name": domain.name,
        "netbios_name": domain.netbios_name,
        "dc_ip": domain.dc_ip,
        "dc_fqdn": domain.dc_fqdn,
        "description": domain.description,
        "trust_type": domain.trust_type,
        "parent_domain_public_id": parent_public_id,
        "extra_data": domain.extra_data or {},
        "updated_at": domain.updated_at.isoformat() if domain.updated_at else None,
        "deleted_at": domain.deleted_at.isoformat() if domain.deleted_at else None,
    }


async def checklist_group_sync_payload(db: AsyncSession, group: ChecklistGroup) -> Dict[str, Any]:
    """Build sync payload for a checklist group (same shape api/checklists emits)."""
    project_public_id = None
    if group.project_id is not None:
        project_public_id = (await db.execute(
            select(Project.public_id).where(Project.id == group.project_id)
        )).scalar_one_or_none()
    return {
        "public_id": group.public_id,
        "project_id": group.project_id,
        "project_public_id": project_public_id,
        "name": group.name,
        "description": group.description,
        "order_index": group.order_index,
        "icon": group.icon,
        "color": group.color,
        "collapsed": group.collapsed,
        "is_template": group.is_template,
        "is_trashed": group.is_trashed,
        "updated_at": group.updated_at.isoformat() if group.updated_at else None,
        "deleted_at": group.deleted_at.isoformat() if group.deleted_at else None,
    }


async def checklist_item_sync_payload(db: AsyncSession, item: ChecklistItem) -> Dict[str, Any]:
    """Build sync payload for a checklist item (same shape api/checklists emits)."""
    group = (await db.execute(
        select(ChecklistGroup).where(ChecklistGroup.id == item.group_id)
    )).scalar_one_or_none()
    project_public_id = None
    if group is not None and group.project_id is not None:
        project_public_id = (await db.execute(
            select(Project.public_id).where(Project.id == group.project_id)
        )).scalar_one_or_none()
    return {
        "public_id": item.public_id,
        "group_id": item.group_id,
        "group_public_id": group.public_id if group else None,
        "project_public_id": project_public_id,
        "name": item.name,
        "description": item.description,
        "command_template": item.command_template,
        "output_regex": item.output_regex,
        "variables": item.variables,
        "input_definitions": item.input_definitions,
        "storage_policy": item.storage_policy,
        "parameter_schema": item.parameter_schema,
        "target_filter": item.target_filter,
        "timeout": item.timeout,
        "alert_patterns": item.alert_patterns,
        "finding_template": item.finding_template,
        "tags": item.tags,
        "enabled": item.enabled,
        "order_index": item.order_index,
        "is_trashed": item.is_trashed,
        "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        "deleted_at": item.deleted_at.isoformat() if item.deleted_at else None,
    }
