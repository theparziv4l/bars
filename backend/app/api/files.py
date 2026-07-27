"""File explorer API endpoints."""
import shlex
from typing import List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Body
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pathlib import Path

from app.database import get_db
from app.models.file import DiscoveredFile
from app.config import settings

router = APIRouter()

CREDENTIAL_KEY_MAP = {
    "username": ["smb_user", "username", "user", "smb_username"],
    "password": ["smb_pass", "password", "pass", "smb_password"],
    "domain": ["smb_domain", "domain", "netbios_domain"],
}


async def _resolve_smb_credentials(db, host, project):
    """Resolve SMB credentials: try AD domain variables first, fall back to project-level."""
    from app.models.ad_domain import ADDomain
    from app.models.variable import ProjectVariable

    matched_domain = None
    creds = {"username": "", "password": "", "domain": ""}
    source = "manual"
    domain_name = None

    fqdn = host.fqdn or ""
    host_domain_hint = (host.extra_data or {}).get("domain", "")

    if fqdn or host_domain_hint:
        result = await db.execute(
            select(ADDomain).where(
                ADDomain.project_id == project.id,
                ADDomain.deleted_at.is_(None),
            )
        )
        ad_domains = result.scalars().all()

        for ad in ad_domains:
            ad_name_lower = (ad.name or "").lower()
            if not ad_name_lower:
                continue
            if (fqdn and fqdn.lower().endswith("." + ad_name_lower)) or \
               (host_domain_hint and host_domain_hint.lower() == ad_name_lower) or \
               (ad.netbios_name and host_domain_hint and host_domain_hint.upper() == ad.netbios_name.upper()):
                matched_domain = ad
                break

    if matched_domain:
        result = await db.execute(
            select(ProjectVariable).where(
                ProjectVariable.project_id == project.id,
                ProjectVariable.ad_domain_id == matched_domain.id,
                ProjectVariable.deleted_at.is_(None),
            )
        )
        domain_vars = {v.key.lower(): v.value for v in result.scalars().all()}

        for cred_field, key_options in CREDENTIAL_KEY_MAP.items():
            for key in key_options:
                if key in domain_vars:
                    creds[cred_field] = str(domain_vars[key])
                    break

        if creds["username"]:
            source = "auto"
            domain_name = matched_domain.name
            if not creds["domain"] and matched_domain.netbios_name:
                creds["domain"] = matched_domain.netbios_name

    if not creds["username"]:
        project_creds = (project.extra_data or {}).get("smb_credentials", {})
        creds["username"] = project_creds.get("username", "")
        creds["password"] = project_creds.get("password", "")
        creds["domain"] = project_creds.get("domain", "")
        source = "project" if creds["username"] else "none"

    return {**creds, "source": source, "domain_name": domain_name}


@router.get("")
async def list_files(
    host_id: int,
    share_name: Optional[str] = None,
    path: Optional[str] = None,
    file_type: Optional[str] = None,
    interesting_only: bool = False,
    search: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(100, ge=1, le=500),
    db: AsyncSession = Depends(get_db)
):
    """List discovered files for a host with pagination."""

    query = select(DiscoveredFile).where(DiscoveredFile.host_id == host_id)
    if share_name:
        query = query.where(DiscoveredFile.share_name == share_name)
    if path:
        query = query.where(DiscoveredFile.path.like(f"{path}%"))
    if file_type:
        query = query.where(DiscoveredFile.file_type == file_type)
    if interesting_only:
        query = query.where(DiscoveredFile.is_interesting == True)
    if search:
        query = query.where(DiscoveredFile.name.ilike(f"%{search}%"))

    subq = query.subquery()
    total = (await db.execute(select(func.count()).select_from(subq))).scalar_one()

    query = query.order_by(DiscoveredFile.path, DiscoveredFile.name)
    query = query.offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(query)
    files = result.scalars().all()

    return {
        "files": [_file_to_dict(f) for f in files],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page if per_page else 0,
    }


@router.get("/tree")
async def get_file_tree(host_id: int, share_name: Optional[str] = None, db: AsyncSession = Depends(get_db)):
    """Get file tree structure for virtual explorer."""
    query = select(DiscoveredFile).where(DiscoveredFile.host_id == host_id)
    if share_name:
        query = query.where(DiscoveredFile.share_name == share_name)
    
    result = await db.execute(query)
    files = result.scalars().all()
    
    # Build tree structure
    tree = {}
    for f in files:
        # Skip share entries when building children (they are the root nodes)
        if f.file_type == "share":
            if f.share_name not in tree:
                tree[f.share_name] = {
                    "name": f.share_name,
                    "type": "share",
                    "children": {},
                    "is_readable": f.is_readable,
                    "is_writable": f.is_writable,
                    "permissions": f.permissions,
                }
            continue
        
        # Ensure share root exists
        if f.share_name not in tree:
            tree[f.share_name] = {"name": f.share_name, "type": "share", "children": {}}
        
        # Parse path parts
        parts = f.path.strip("/").split("/") if f.path.strip("/") else []
        current = tree[f.share_name]
        
        # Navigate/create directory structure, inheriting is_writable from share
        share_writable = tree[f.share_name].get("is_writable", False)
        for part in parts:
            if part:
                if "children" not in current:
                    current["children"] = {}
                current = current["children"].setdefault(part, {
                    "name": part, "type": "directory", "children": {},
                    "is_writable": share_writable,
                })
        
        # Add the file/directory entry
        if "children" not in current:
            current["children"] = {}
        current["children"][f.name] = {
            "id": f.id, "name": f.name, "type": f.file_type,
            "size": f.size, "is_interesting": f.is_interesting,
            "interest_reason": f.interest_reason,
            "last_modified": (f.extra_data or {}).get("date"),
            "local_path": f.local_path,
        }
    
    return {"tree": tree}


@router.get("/shares")
async def list_shares(host_id: int, db: AsyncSession = Depends(get_db)):
    """List discovered shares for a host with detailed metadata."""
    # Get all share entries (file_type = 'share')
    result = await db.execute(
        select(DiscoveredFile).where(
            DiscoveredFile.host_id == host_id,
            DiscoveredFile.file_type == "share"
        )
    )
    share_entries = result.scalars().all()
    
    # If no explicit share entries, fall back to distinct share names
    if not share_entries:
        result = await db.execute(
            select(DiscoveredFile.share_name).where(
                DiscoveredFile.host_id == host_id
            ).distinct()
        )
        share_names = [r[0] for r in result.all()]
        return {
            "shares": [{"name": name, "access_level": "unknown", "type": "smb"} for name in share_names]
        }
    
    # Count files per share
    file_counts = {}
    count_result = await db.execute(
        select(DiscoveredFile.share_name, DiscoveredFile.id)
        .where(DiscoveredFile.host_id == host_id, DiscoveredFile.file_type != "share")
    )
    for row in count_result.all():
        share_name = row[0]
        file_counts[share_name] = file_counts.get(share_name, 0) + 1
    
    shares = []
    for share in share_entries:
        shares.append({
            "id": share.id,
            "name": share.share_name,
            "access_level": share.permissions.get("access_level", "unknown") if share.permissions else "unknown",
            "type": share.permissions.get("share_type", "smb") if share.permissions else "smb",
            "comment": share.extra_data.get("comment", "") if share.extra_data else "",
            "is_readable": share.is_readable,
            "is_writable": share.is_writable,
            "file_count": file_counts.get(share.share_name, 0),
            "discovered_at": share.discovered_at.isoformat() if share.discovered_at else None,
        })
    
    return {"shares": shares}


@router.get("/browse-local")
async def browse_local(path: str = Query("/home/kali")):
    """Browse local filesystem directories for the file picker via host agent."""
    import json
    from app.core.host_runner import host_runner

    # Use host_runner to list directory on the host (not inside Docker)
    safe_path = path.replace("'", "'\"'\"'")
    cmd = f'''python3 -c "
import os, json, sys
target = os.path.realpath('{safe_path}')
if not os.path.exists(target):
    print(json.dumps({{'error': 'Path not found'}}))
    sys.exit(0)
if not os.path.isdir(target):
    print(json.dumps({{'error': 'Not a directory'}}))
    sys.exit(0)
entries = []
try:
    for name in sorted(os.listdir(target)):
        if name.startswith('.'):
            continue
        full = os.path.join(target, name)
        try:
            st = os.stat(full)
            entries.append({{'name': name, 'path': full, 'is_dir': os.path.isdir(full), 'size': st.st_size if os.path.isfile(full) else None}})
        except (PermissionError, OSError):
            continue
except PermissionError:
    print(json.dumps({{'error': 'Permission denied'}}))
    sys.exit(0)
entries.sort(key=lambda e: (not e['is_dir'], e['name'].lower()))
parent = os.path.dirname(target) if target != '/' else None
print(json.dumps({{'current_path': target, 'parent_path': parent, 'entries': entries}}))
"'''

    result = await host_runner.execute_sync(cmd)
    if result["exit_code"] != 0:
        raise HTTPException(status_code=500, detail=f"Browse failed: {result['stderr']}")

    try:
        data = json.loads(result["stdout"].strip())
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Failed to parse directory listing")

    if "error" in data:
        raise HTTPException(status_code=404, detail=data["error"])

    return data


@router.get("/resolve-smb-credentials")
async def resolve_smb_credentials(host_id: int, db: AsyncSession = Depends(get_db)):
    """Resolve SMB credentials for a host: auto from AD domain variables, fallback to project-level."""
    from app.models.host import Host
    from app.models.project import Project

    host_result = await db.execute(select(Host).where(Host.id == host_id))
    host = host_result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    proj_result = await db.execute(select(Project).where(Project.id == host.project_id))
    project = proj_result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    return await _resolve_smb_credentials(db, host, project)


@router.get("/{file_id}")
async def get_file_details(file_id: int, db: AsyncSession = Depends(get_db)):
    """Get file details."""
    result = await db.execute(select(DiscoveredFile).where(DiscoveredFile.id == file_id))
    file = result.scalar_one_or_none()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")
    return _file_to_dict(file)


@router.get("/{file_id}/download")
async def download_file(file_id: int, db: AsyncSession = Depends(get_db)):
    """Download a discovered file."""
    result = await db.execute(select(DiscoveredFile).where(DiscoveredFile.id == file_id))
    file = result.scalar_one_or_none()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")
    
    if not file.local_path:
        raise HTTPException(status_code=400, detail="File not downloaded locally")
    
    local_path = Path(file.local_path)
    if not local_path.exists():
        raise HTTPException(status_code=404, detail="Local file not found")
    
    return FileResponse(local_path, filename=file.name)


@router.post("/{file_id}/fetch")
async def fetch_file(file_id: int, data: Optional[dict] = Body(None), db: AsyncSession = Depends(get_db)):
    """Download a discovered file from remote SMB share to local project directory."""
    from app.models.host import Host
    from app.models.project import Project
    from app.core.host_runner import host_runner
    from datetime import datetime
    import os

    data = data or {}

    result = await db.execute(select(DiscoveredFile).where(DiscoveredFile.id == file_id))
    file = result.scalar_one_or_none()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    host_result = await db.execute(select(Host).where(Host.id == file.host_id))
    host = host_result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    proj_result = await db.execute(select(Project).where(Project.id == host.project_id))
    project = proj_result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if data.get("username"):
        username = data["username"]
        password = data.get("password", "")
        domain = data.get("domain", "")
    else:
        resolved = await _resolve_smb_credentials(db, host, project)
        username = resolved["username"]
        password = resolved["password"]
        domain = resolved["domain"]

    if not username:
        raise HTTPException(status_code=400, detail="No SMB credentials configured. Set credentials in Files tab settings or configure AD domain variables.")

    # Build local download path: project_path/files/host_ip/share/path/filename
    from app.core.utils import resolve_project_path
    project_path = resolve_project_path(project)

    host_identifier = host.ip_address or host.hostname or f"host_{host.id}"
    remote_path = file.path.strip("/") if file.path else ""
    local_dir = os.path.join(project_path, "files", host_identifier, file.share_name or "unknown")
    if remote_path:
        local_dir = os.path.join(local_dir, remote_path)
    local_file_path = os.path.join(local_dir, file.name)

    # Create local directory
    mkdir_result = await host_runner.execute_sync(f'mkdir -p "{local_dir}"')
    if mkdir_result["exit_code"] != 0:
        raise HTTPException(status_code=500, detail=f"Failed to create directory: {mkdir_result['stderr']}")

    # Build smbclient command
    target_host = host.ip_address or host.hostname
    share_name = file.share_name or ""
    smb_remote_path = f"{remote_path}/{file.name}" if remote_path else file.name
    # Normalize path separators for smbclient
    smb_remote_path = smb_remote_path.replace("/", "\\\\")

    auth_part = f"{domain}\\{username}%{password}" if domain else f"{username}%{password}"
    smb_remote_q = smb_remote_path.replace('"', '\\"')
    local_file_q = local_file_path.replace('"', '\\"')
    cmd = f'smbclient "//{target_host}/{share_name}" -U {shlex.quote(auth_part)} -c "get \\"{smb_remote_q}\\" \\"{local_file_q}\\""'

    result = await host_runner.execute_sync(cmd)
    if result["exit_code"] != 0:
        raise HTTPException(status_code=500, detail=f"Download failed: {result['stderr'] or result['stdout']}")

    # Update DB record
    file.local_path = local_file_path
    file.downloaded_at = datetime.utcnow()
    await db.flush()
    await db.refresh(file)

    return {
        "status": "downloaded",
        "file_id": file_id,
        "local_path": local_file_path,
    }


@router.get("/{file_id}/serve")
async def serve_local_file(file_id: int, db: AsyncSession = Depends(get_db)):
    """Serve an already-downloaded file to the browser."""
    result = await db.execute(select(DiscoveredFile).where(DiscoveredFile.id == file_id))
    file = result.scalar_one_or_none()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    if not file.local_path:
        raise HTTPException(status_code=400, detail="File not downloaded locally yet")

    local_path = Path(file.local_path)
    if not local_path.exists():
        raise HTTPException(status_code=404, detail="Local file not found on disk")

    return FileResponse(str(local_path), filename=file.name)


@router.post("/upload")
async def upload_to_share(data: dict, db: AsyncSession = Depends(get_db)):
    """Upload a local file to a writable SMB share."""
    from app.models.host import Host
    from app.models.project import Project
    from app.core.host_runner import host_runner

    host_id = data.get("host_id")
    share_name = data.get("share_name")
    remote_path = data.get("remote_path", "")
    local_file_path = data.get("local_file_path")

    if not all([host_id, share_name, local_file_path]):
        raise HTTPException(status_code=400, detail="host_id, share_name, and local_file_path are required")

    host_result = await db.execute(select(Host).where(Host.id == host_id))
    host = host_result.scalar_one_or_none()
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")

    proj_result = await db.execute(select(Project).where(Project.id == host.project_id))
    project = proj_result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    if data.get("username"):
        username = data["username"]
        password = data.get("password", "")
        domain = data.get("domain", "")
    else:
        resolved = await _resolve_smb_credentials(db, host, project)
        username = resolved["username"]
        password = resolved["password"]
        domain = resolved["domain"]

    if not username:
        raise HTTPException(status_code=400, detail="No SMB credentials configured. Set credentials in Files tab settings or configure AD domain variables.")

    target_host = host.ip_address or host.hostname
    auth_part = f"{domain}\\{username}%{password}" if domain else f"{username}%{password}"

    import os
    filename = os.path.basename(local_file_path)

    local_q = local_file_path.replace('"', '\\"')
    filename_q = filename.replace('"', '\\"')
    if remote_path:
        remote_dir = remote_path.replace("/", "\\\\")
        remote_dir_q = remote_dir.replace('"', '\\"')
        cmd = f'smbclient "//{target_host}/{share_name}" -U {shlex.quote(auth_part)} -c "cd \\"{remote_dir_q}\\"; put \\"{local_q}\\" \\"{filename_q}\\""'
    else:
        cmd = f'smbclient "//{target_host}/{share_name}" -U {shlex.quote(auth_part)} -c "put \\"{local_q}\\" \\"{filename_q}\\""'

    result = await host_runner.execute_sync(cmd)
    if result["exit_code"] != 0:
        raise HTTPException(status_code=500, detail=f"Upload failed: {result['stderr'] or result['stdout']}")

    return {"status": "uploaded", "message": f"File uploaded to //{target_host}/{share_name}"}


TEXT_EXTENSIONS = {
    "txt", "xml", "conf", "ini", "cfg", "yaml", "yml", "json", "log", "md",
    "csv", "bat", "ps1", "sh", "py", "html", "htm", "css", "js", "ts",
    "toml", "env", "properties", "sql", "rb", "pl", "php", "java", "c", "h",
    "cpp", "hpp", "cs", "go", "rs", "vbs", "reg", "inf", "pol", "psm1",
    "psd1", "cmd", "jsx", "tsx",
}

MAX_TEXT_SIZE = 1 * 1024 * 1024  # 1 MB


@router.get("/{file_id}/content")
async def get_file_content(file_id: int, db: AsyncSession = Depends(get_db)):
    """Read text content of a downloaded file for in-browser viewing."""
    from app.core.host_runner import host_runner

    result = await db.execute(select(DiscoveredFile).where(DiscoveredFile.id == file_id))
    file = result.scalar_one_or_none()
    if not file:
        raise HTTPException(status_code=404, detail="File not found")

    if not file.local_path:
        raise HTTPException(status_code=400, detail="File not downloaded locally yet")

    ext = file.name.rsplit(".", 1)[-1].lower() if "." in file.name else ""
    if ext not in TEXT_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"File type '.{ext}' is not viewable as text")

    safe_path = file.local_path.replace("'", "'\"'\"'")
    read_cmd = f"head -c {MAX_TEXT_SIZE} '{safe_path}'"
    read_result = await host_runner.execute_sync(read_cmd)
    if read_result["exit_code"] != 0:
        raise HTTPException(status_code=500, detail=f"Failed to read file: {read_result['stderr']}")

    content = read_result["stdout"]
    return {
        "filename": file.name,
        "extension": ext,
        "content": content,
        "size": file.size,
        "truncated": file.size is not None and file.size > MAX_TEXT_SIZE,
    }


@router.get("/search/global")
async def search_files(
    project_id: int,
    query: str,
    file_type: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """Search files across all hosts in a project."""
    from app.models.host import Host
    
    # Get hosts in project
    host_result = await db.execute(select(Host.id).where(Host.project_id == project_id))
    host_ids = [r[0] for r in host_result.all()]
    
    if not host_ids:
        return {"files": []}
    
    file_query = select(DiscoveredFile).where(
        DiscoveredFile.host_id.in_(host_ids),
        DiscoveredFile.name.ilike(f"%{query}%")
    )
    
    if file_type:
        file_query = file_query.where(DiscoveredFile.file_type == file_type)
    
    result = await db.execute(file_query.limit(100))
    files = result.scalars().all()
    
    return {"files": [_file_to_dict(f) for f in files]}


def _file_to_dict(file: DiscoveredFile) -> dict:
    return {
        "id": file.id, "host_id": file.host_id, "execution_id": file.execution_id,
        "share_name": file.share_name, "path": file.path, "name": file.name,
        "file_type": file.file_type, "size": file.size,
        "permissions": file.permissions, "is_readable": file.is_readable,
        "is_writable": file.is_writable, "is_interesting": file.is_interesting,
        "interest_reason": file.interest_reason, "content_preview": file.content_preview,
        "local_path": file.local_path, "downloaded_at": file.downloaded_at,
        "discovered_at": file.discovered_at, "full_path": file.full_path,
        "last_modified": (file.extra_data or {}).get("date"),
        "extra_data": file.extra_data,
    }

