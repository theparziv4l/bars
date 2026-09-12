"""Nmap NSE script scan for discovered services: service-to-wildcard mapping and checklist generation."""
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.host import Host, Service
from app.models.checklist import ChecklistItem, ChecklistGroup
from app.core.orchestrator import task_orchestrator
from app.core.sync import sync_service, execution_sync_payload

if TYPE_CHECKING:
    from fastapi import BackgroundTasks

# Service name (from Nmap/tool output) -> NSE wildcard pattern
SERVICE_SCRIPT_MAP: Dict[str, str] = {
    "http": "http*",
    "https": "http*,ssl*",
    "ssl": "ssl*",
    "ssh": "ssh*",
    "smb": "smb*",
    "microsoft-ds": "smb*",
    "netbios-ssn": "smb*",
    "ftp": "ftp*",
    "smtp": "smtp*",
    "mysql": "mysql*",
    "mssql": "ms-sql*",
    "ms-sql-s": "ms-sql*",
    "rdp": "rdp*",
    "ms-wbt-server": "rdp*",
    "dns": "dns*",
    "snmp": "snmp*",
    "ldap": "ldap*",
    "nfs": "nfs*",
    "vnc": "vnc*",
    "telnet": "telnet*",
    "pop3": "pop3*",
    "imap": "imap*",
    "kerberos": "krb5*",
    "domain": "dns*",
    "msrpc": "msrpc*",
    "ajp13": "ajp*",
}

# Service name -> canonical tag (for host tagging and target_filter)
SERVICE_TAG_MAP: Dict[str, str] = {
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

SERVICE_SCAN_GROUP_NAME = "Nmap Script Scan"
NSE_DEFAULT_ALERT_PATTERNS = {
    "critical": ["VULNERABLE", r"CVE-\d{4}-\d+"],
    "warning": ["potentially risky", "Anonymous"],
}


def _tag_from_service(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    return SERVICE_TAG_MAP.get(name.lower().strip())


def _scripts_for_service(name: Optional[str]) -> Optional[str]:
    if not name:
        return None
    return SERVICE_SCRIPT_MAP.get(name.lower().strip())


async def generate_service_scan_items(
    db: AsyncSession,
    project_id: int,
    service_names: Optional[List[str]] = None,
    timing: int = 4,
    extra_args: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create or update a "Nmap Script Scan" checklist group with one item per service type.
    Each item uses target_filter so {targets} is resolved from tagged hosts at execution time.
    """
    result = await db.execute(
        select(Host)
        .where(
            Host.project_id == project_id,
            Host.deleted_at.is_(None),
            # Out-of-scope hosts must not inflate a tag's port set or host count —
            # the generated items resolve {targets} from those same tags at run time.
            Host.excluded.is_(False),
        )
        .options(selectinload(Host.services))
    )
    hosts = result.scalars().all()

    # Collect per-tag: scripts pattern, set of ports, host count
    tag_data: Dict[str, Dict[str, Any]] = {}
    skipped_services: List[str] = []

    for host in hosts:
        if not host.services:
            continue
        for svc in host.services:
            if svc.state != "open":
                continue
            if service_names is not None and svc.name and svc.name.lower() not in [s.lower() for s in service_names]:
                continue
            tag = _tag_from_service(svc.name)
            scripts = _scripts_for_service(svc.name)
            if not tag or not scripts:
                if svc.name and svc.name.lower() not in [s.lower() for s in skipped_services]:
                    skipped_services.append(svc.name or "unknown")
                continue
            if tag not in tag_data:
                tag_data[tag] = {"scripts": scripts, "ports": set(), "host_count": set()}
            tag_data[tag]["ports"].add(svc.port)
            tag_data[tag]["host_count"].add(host.ip_address)

    if not tag_data:
        return {
            "group_id": 0,
            "items": [],
            "skipped_services": skipped_services,
        }

    # Find or create "Nmap Script Scan" group
    group_result = await db.execute(
        select(ChecklistGroup).where(
            ChecklistGroup.project_id == project_id,
            ChecklistGroup.name == SERVICE_SCAN_GROUP_NAME,
        )
    )
    group = group_result.scalar_one_or_none()
    if not group:
        group = ChecklistGroup(
            project_id=project_id,
            name=SERVICE_SCAN_GROUP_NAME,
            description="Nmap NSE script scans per service type (targets resolved by host tags)",
            order_index=0,
        )
        db.add(group)
        await db.flush()

    # Build command template with optional extra_args
    base_template = "nmap -sV --script {scripts} -p {ports} -iL {targets} -T{timing}"
    if extra_args:
        base_template += f" {extra_args}"

    created_items: List[Dict[str, Any]] = []
    order_index = 0

    for tag, data in sorted(tag_data.items()):
        ports_str = ",".join(str(p) for p in sorted(data["ports"]))
        scripts_str = data["scripts"]
        tag_display = tag.upper() if len(tag) <= 4 else tag.capitalize()
        item_name = f"NSE {tag_display} Scan"

        # Check if item for this tag already exists
        existing = await db.execute(
            select(ChecklistItem).where(
                ChecklistItem.group_id == group.id,
                ChecklistItem.name == item_name,
            )
        )
        existing_item = existing.scalar_one_or_none()
        if existing_item:
            existing_item.command_template = base_template
            existing_item.variables = {"scripts": scripts_str, "ports": ports_str, "timing": timing}
            existing_item.target_filter = {"tags": [tag]}
            existing_item.parameter_schema = {"targets": {"type": "file"}}
            existing_item.output_regex = {"nse_findings": r"\|\s+(\S+):\s+(.+)"}
            existing_item.alert_patterns = NSE_DEFAULT_ALERT_PATTERNS
            db.add(existing_item)
            created_items.append({
                "id": existing_item.id,
                "name": existing_item.name,
                "command_template": existing_item.command_template,
                "scripts": scripts_str,
                "ports": ports_str,
                "tag": tag,
                "host_count": len(data["host_count"]),
            })
        else:
            item = ChecklistItem(
                group_id=group.id,
                name=item_name,
                description=f"Nmap NSE scripts for {tag} on discovered hosts (targets from tag filter)",
                command_template=base_template,
                variables={"scripts": scripts_str, "ports": ports_str, "timing": timing},
                target_filter={"tags": [tag]},
                parameter_schema={"targets": {"type": "file"}},
                output_regex={"nse_findings": r"\|\s+(\S+):\s+(.+)"},
                alert_patterns=NSE_DEFAULT_ALERT_PATTERNS,
                order_index=order_index,
            )
            db.add(item)
            await db.flush()
            created_items.append({
                "id": item.id,
                "name": item.name,
                "command_template": item.command_template,
                "scripts": scripts_str,
                "ports": ports_str,
                "tag": tag,
                "host_count": len(data["host_count"]),
            })
        order_index += 1

    await db.flush()
    return {
        "group_id": group.id,
        "items": created_items,
        "skipped_services": skipped_services,
    }


async def launch_host_script_scan(
    db: AsyncSession,
    host_id: int,
    service_names: Optional[List[str]] = None,
    timing: int = 4,
    extra_args: Optional[str] = None,
    background_tasks: Optional["BackgroundTasks"] = None,
) -> Dict[str, Any]:
    """
    Run a single nmap script scan for one host, combining all its service wildcards.
    Returns execution info; results appear in host execution history.
    """
    result = await db.execute(
        select(Host).where(Host.id == host_id).options(selectinload(Host.services))
    )
    host = result.scalar_one_or_none()
    if not host:
        raise ValueError("Host not found")
    if host.excluded:
        raise ValueError("Host is excluded from scope")
    if not host.project_id:
        raise ValueError("Host has no project")

    project_id = host.project_id
    scripts_set: set = set()
    ports_set: set = set()

    for svc in host.services or []:
        if svc.state != "open":
            continue
        if service_names is not None and svc.name and svc.name.lower() not in [s.lower() for s in service_names]:
            continue
        sp = _scripts_for_service(svc.name)
        if sp:
            for part in sp.split(","):
                scripts_set.add(part.strip())
            ports_set.add(svc.port)

    if not scripts_set or not ports_set:
        raise ValueError("No mapped services found for this host")

    scripts_str = ",".join(sorted(scripts_set))
    ports_str = ",".join(str(p) for p in sorted(ports_set))
    target = host.ip_address or host.hostname or str(host_id)
    base_cmd = f"nmap -sV --script {scripts_str} -p {ports_str} {target} -T{timing}"
    if extra_args:
        base_cmd += f" {extra_args}"

    # Find or create a "Host Script Scan" item in the project for tracking
    group_result = await db.execute(
        select(ChecklistGroup).where(
            ChecklistGroup.project_id == project_id,
            ChecklistGroup.name == SERVICE_SCAN_GROUP_NAME,
        )
    )
    group = group_result.scalar_one_or_none()
    if not group:
        group = ChecklistGroup(
            project_id=project_id,
            name=SERVICE_SCAN_GROUP_NAME,
            description="Nmap NSE script scans",
            order_index=0,
        )
        db.add(group)
        await db.flush()

    item_result = await db.execute(
        select(ChecklistItem).where(
            ChecklistItem.group_id == group.id,
            ChecklistItem.name == "Host Script Scan",
        )
    )
    item = item_result.scalar_one_or_none()
    if not item:
        item = ChecklistItem(
            group_id=group.id,
            name="Host Script Scan",
            description="Per-host NSE script scan (single host)",
            command_template="nmap -sV --script {scripts} -p {ports} {target} -T{timing}",
            variables={"scripts": "", "ports": "", "timing": timing, "target": ""},
            order_index=9999,
        )
        db.add(item)
        await db.flush()

    execution = await task_orchestrator.create_execution_record(
        db=db,
        item_id=item.id,
        host_id=host_id,
        variables={},
        command_override=base_cmd,
    )
    payload = await execution_sync_payload(db, execution)
    await sync_service.record_event(
        db,
        entity_type="executions",
        entity_public_id=execution.public_id,
        operation="create",
        payload=payload,
    )
    await db.commit()

    if background_tasks:
        background_tasks.add_task(
            task_orchestrator.run_execution_background,
            execution.id,
            item.id,
            host_id,
            {},
            None,
            None,
            base_cmd,
        )

    return {
        "execution_id": execution.id,
        "command": base_cmd,
        "host_id": host_id,
    }
