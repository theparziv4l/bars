"""Shared utilities."""
import os
import re
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.project import Project


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
