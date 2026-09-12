"""Shared utilities."""
import os
import re
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.project import Project


def is_unset(value) -> bool:
    """True when *value* carries no usable content.

    Callers (UI, flow steps, library seeds) routinely send a variable KEY with an
    empty value — the checklist item declares ``{"targets": ""}`` and the seeded
    library ships project variables like ``targets = ""``. Presence checks
    (``"targets" in variables``) therefore see a value that isn't one, and skip
    resolution. Use this instead of ``in`` whenever a key gates resolution.

    ``0``/``False`` are real values and are NOT unset.
    """
    return value is None or value == "" or value == [] or value == {}


def merge_set_values(base: dict, incoming: dict | None) -> dict:
    """Update *base* with the entries of *incoming* that actually carry a value.

    Prevents a caller-supplied ``{"target": ""}`` from blanking a resolved project
    variable of the same name.
    """
    base.update({k: v for k, v in (incoming or {}).items() if not is_unset(v)})
    return base


def get_project_path(project_name: str, base_path: str | None = None) -> str:
    """
    Generate a safe project directory path from the project name.

    Args:
        project_name: The raw project name.
        base_path: Optional base directory for all projects; if unset, uses
            settings.PROJECTS_BASE_PATH (default /home/kali/Pentests).

    Returns:
        Absolute path to the project directory.
    """
    if base_path is None:
        from app.config import settings
        base_path = settings.PROJECTS_BASE_PATH
    if not project_name:
        raise ValueError("Project name cannot be empty")

    safe_name = re.sub(r'[^a-zA-Z0-9_-]', '_', project_name)
    safe_name = re.sub(r'_+', '_', safe_name).strip('_')

    if not safe_name:
        safe_name = "unnamed_project"

    return os.path.join(base_path, safe_name)


async def excluded_targets(db, project_id: Optional[int]) -> set:
    """Every identifier the out-of-scope hosts of *project_id* answer to.

    Hosts flagged ``excluded`` are an ROE carve-out: they keep their services,
    findings and history, but no execution may be aimed at them. Target lists reach
    the orchestrator as bare strings — the UI injects ``ip_address or display_name``,
    and display_name falls back to hostname/fqdn — so matching on ip_address alone
    would let a hostname-shaped target slip past the filter.

    Returns an empty set when *project_id* is unknown, so callers can filter
    unconditionally.
    """
    if not project_id:
        return set()

    from sqlalchemy import select
    from app.models.host import Host

    rows = await db.execute(
        select(Host.ip_address, Host.hostname, Host.fqdn).where(
            Host.project_id == project_id,
            Host.excluded.is_(True),
            Host.deleted_at.is_(None),
        )
    )
    return {value for row in rows.all() for value in row if value}


def as_target_list(value) -> list:
    """Normalise a target value to a list.

    Targets reach the orchestrator as a list (host picker), a whitespace-separated
    string (manual entry, project variables) or a bare scalar.
    """
    if isinstance(value, str):
        return [v.strip() for v in value.split() if v.strip()]
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value] if value else []


def drop_excluded(value, excluded: set) -> list:
    """Filter a target value by *excluded*, returning a list."""
    return [v for v in as_target_list(value) if str(v) not in excluded]


def resolve_project_path(project: "Project") -> str:
    """Return the host-side project directory for *project*.

    Prefers the value stored in ``project.extra_data["project_path"]`` but
    validates it first — if it contains un-interpolated placeholders (e.g.
    ``${CURRENT_USER_HOME_DIR}``) or is empty, falls back to regenerating
    the path from the project name via :func:`get_project_path`.
    """
    stored = (project.extra_data or {}).get("project_path", "")
    if stored and "$" not in stored:
        return stored
    return get_project_path(project.name)



def parse_network_interfaces(ip_data: list) -> dict:
    """
    Parse output from 'ip -j addr' to get IPv4 addresses.
    Excludes loopback and interfaces without IPv4.
    
    Args:
        ip_data: List of interface dicts from 'ip -j addr'
    
    Returns:
        Dictionary mapping interface name to IP address.
        Example: {"eth0": "192.168.1.5", "tun0": "10.10.10.2"}
    """
    interfaces = {}
    
    try:
        for iface in ip_data:
            name = iface.get("ifname")
            if not name:
                continue
                
            # Skip loopback
            if name.lower().startswith("lo") or iface.get("link_type") == "loopback":
                continue
            
            # Find IPv4 address
            for addr_info in iface.get("addr_info", []):
                if addr_info.get("family") == "inet":
                    interfaces[name] = addr_info.get("local")
                    break
                    
    except Exception as e:
        print(f"Error parsing network interfaces: {e}")
        return {}
        
    return interfaces
