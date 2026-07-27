"""Search API endpoints with global regex search."""
import re
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_

from app.database import get_db
from app.models.execution import Execution
from app.models.host import Host
from app.models.file import DiscoveredFile
from app.models.checklist import ChecklistItem, ChecklistGroup

router = APIRouter()


@router.get("")
async def global_search(
    project_id: int,
    query: str,
    is_regex: bool = False,
    search_in: str = "logs,files,hosts",
    limit: int = 500,
    db: AsyncSession = Depends(get_db)
):
    """
    Global search across all project data.
    
    Args:
        project_id: Project to search in
        query: Search query (plain text or regex)
        is_regex: If true, treat query as regex
        search_in: Comma-separated list of what to search (logs, files, hosts)
        limit: Maximum results
    """
    # Accept both a CSV string (query param) and a list (internal callers like
    # /search/logs), so the dedicated log-search endpoint doesn't 500 on .split().
    if isinstance(search_in, str):
        search_in = [s.strip() for s in search_in.split(",") if s.strip()]
    results = {"logs": [], "files": [], "hosts": [], "total": 0}
    
    # Get hosts in project for file/host search
    host_result = await db.execute(select(Host.id).where(Host.project_id == project_id))
    host_ids = [r[0] for r in host_result.all()]
    
    # Compile regex if needed
    pattern = None
    if is_regex:
        try:
            pattern = re.compile(query, re.IGNORECASE | re.MULTILINE)
        except re.error as e:
            raise HTTPException(status_code=400, detail=f"Invalid regex: {str(e)}")
    
    # Search in execution logs
    if "logs" in search_in:
        # Log search is scoped by Project -> ChecklistGroup -> ChecklistItem -> Execution
        # This includes global executions (null host_id) and deleted host executions
        item_query = select(ChecklistItem.id).join(ChecklistGroup).where(ChecklistGroup.project_id == project_id)
        item_result = await db.execute(item_query)
        item_ids = [r[0] for r in item_result.all()]
        
        if item_ids:
            exec_query = select(Execution).where(Execution.item_id.in_(item_ids))
            
            if is_regex and pattern:
                # Use SQLite REGEXP for server-side filtering with hard cap
                exec_query = exec_query.where(
                    or_(
                        Execution.stdout.regexp_match(query),
                        Execution.stderr.regexp_match(query),
                        Execution.command.regexp_match(query),
                    )
                ).order_by(Execution.id.desc()).limit(limit)
            else:
                exec_query = exec_query.where(
                    or_(
                        Execution.stdout.ilike(f"%{query}%"),
                        Execution.stderr.ilike(f"%{query}%"),
                        Execution.command.ilike(f"%{query}%")
                    )
                ).order_by(Execution.id.desc()).limit(limit)
                
            exec_result = await db.execute(exec_query)
            executions = exec_result.scalars().all()
            
            for execution in executions:
                matches = []
                for field, content in [("command", execution.command), ("stdout", execution.stdout), ("stderr", execution.stderr)]:
                    if not content:
                        continue
                    if is_regex and pattern:
                        for match in pattern.finditer(content):
                            line_start = content.rfind('\n', 0, match.start()) + 1
                            line_end = content.find('\n', match.end())
                            line = content[line_start:line_end if line_end != -1 else None].strip()
                            matches.append({"field": field, "line": line, "match": match.group()})
                    elif query.lower() in content.lower():
                        idx = content.lower().find(query.lower())
                        start = max(0, idx - 40)
                        end = min(len(content), idx + len(query) + 40)
                        snippet = content[start:end].strip()
                        if start > 0: snippet = "..." + snippet
                        if end < len(content): snippet = snippet + "..."
                        matches.append({"field": field, "line": snippet, "match": query})
                
                if matches:
                    results["logs"].append({
                        "execution_id": execution.id,
                        "item_id": execution.item_id,
                        "host_id": execution.host_id,
                        "command": execution.command,
                        "matches": matches[:10]
                    })
    
    # Search in files
    if "files" in search_in:
        file_query = select(DiscoveredFile).where(DiscoveredFile.host_id.in_(host_ids))
        
        if is_regex:
            file_query = file_query.where(
                or_(
                    DiscoveredFile.name.regexp_match(query),
                    DiscoveredFile.path.regexp_match(query),
                )
            ).limit(limit)
            file_result = await db.execute(file_query)
            results["files"] = [
                {"id": f.id, "host_id": f.host_id, "name": f.name, "path": f.path, "share_name": f.share_name}
                for f in file_result.scalars().all()
            ]
        else:
            file_query = file_query.where(
                or_(
                    DiscoveredFile.name.ilike(f"%{query}%"),
                    DiscoveredFile.path.ilike(f"%{query}%")
                )
            ).limit(limit)
            file_result = await db.execute(file_query)
            results["files"] = [
                {"id": f.id, "host_id": f.host_id, "name": f.name, "path": f.path, "share_name": f.share_name}
                for f in file_result.scalars().all()
            ]
    
    # Search in hosts
    if "hosts" in search_in:
        host_query = select(Host).where(Host.project_id == project_id)
        
        if not is_regex:
            host_query = host_query.where(
                or_(
                    Host.ip_address.ilike(f"%{query}%"),
                    Host.hostname.ilike(f"%{query}%"),
                    Host.fqdn.ilike(f"%{query}%"),
                    Host.notes.ilike(f"%{query}%")
                )
            ).limit(limit)
        else:
            host_query = host_query.limit(limit * 5)

        host_result = await db.execute(host_query)
        hosts = host_result.scalars().all()

        for host in hosts:
            if len(results["hosts"]) >= limit:
                break
            if is_regex:
                searchable = f"{host.ip_address} {host.hostname} {host.fqdn} {host.notes or ''}"
                if pattern.search(searchable):
                    results["hosts"].append({
                        "id": host.id, "ip_address": host.ip_address,
                        "hostname": host.hostname, "fqdn": host.fqdn
                    })
            else:
                results["hosts"].append({
                    "id": host.id, "ip_address": host.ip_address,
                    "hostname": host.hostname, "fqdn": host.fqdn
                })
    
    results["total"] = len(results["logs"]) + len(results["files"]) + len(results["hosts"])
    return results


@router.get("/logs")
async def search_logs(
    project_id: int,
    query: str,
    is_regex: bool = False,
    item_id: Optional[int] = None,
    host_id: Optional[int] = None,
    limit: int = 50,
    db: AsyncSession = Depends(get_db)
):
    """Search specifically in execution logs."""
    return await global_search(
        project_id=project_id, query=query, is_regex=is_regex,
        search_in=["logs"], limit=limit, db=db
    )
