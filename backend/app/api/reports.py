"""Report generation API endpoints."""
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import get_db
from app.models.project import Project
from app.models.host import Host
from app.models.checklist import ChecklistGroup
from app.models.execution import Execution
from app.models.finding import Finding, FindingSeverity, FindingStatus, SEVERITY_ORDER
from app.models.evidence import Evidence
from app.config import settings

router = APIRouter()


def _write_report_sync(path: Path, content: str) -> None:
    """Sync file write for report content (run in thread pool)."""
    path.write_text(content)


def _render_pdf_sync(markdown_content: str, pdf_path: Path) -> None:
    """Sync markdown-to-HTML and WeasyPrint PDF render (run in thread pool)."""
    from weasyprint import HTML
    html = _markdown_to_html(markdown_content)
    HTML(string=html).write_pdf(pdf_path)


def _list_reports_sync(reports_path: Path) -> list:
    """Sync list of report files with metadata (run in thread pool)."""
    reports = []
    for path in reports_path.glob("*.md"):
        reports.append({
            "name": path.name,
            "size": path.stat().st_size,
            "created": datetime.fromtimestamp(path.stat().st_mtime).isoformat()
        })
    for path in reports_path.glob("*.pdf"):
        reports.append({
            "name": path.name,
            "size": path.stat().st_size,
            "created": datetime.fromtimestamp(path.stat().st_mtime).isoformat()
        })
    return sorted(reports, key=lambda x: x["created"], reverse=True)


@router.post("/generate")
async def generate_report(
    project_id: int,
    format: str = "markdown",
    include_logs: bool = True,
    include_files: bool = True,
    db: AsyncSession = Depends(get_db)
):
    """Generate a report for a project (markdown | html | pdf), driven by Findings."""
    result = await db.execute(
        select(Project).where(Project.id == project_id)
        .options(
            selectinload(Project.hosts).selectinload(Host.services),
            selectinload(Project.hosts).selectinload(Host.executions),
            selectinload(Project.checklist_groups).selectinload(ChecklistGroup.items),
        )
    )
    project = result.scalar_one_or_none()
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")

    # Load findings (exclude false-positives), severity-desc, for the report body + rollup.
    finding_rows = (await db.execute(
        select(Finding).where(
            Finding.project_id == project_id,
            Finding.deleted_at.is_(None),
            Finding.status != FindingStatus.FALSE_POSITIVE,
        )
    )).scalars().all()
    findings = sorted(finding_rows, key=lambda f: (SEVERITY_ORDER.get(f.severity, 0), f.title), reverse=True)
    rollup = {s.value: 0 for s in FindingSeverity}
    for f in findings:
        rollup[f.severity.value] += 1
    rollup["total"] = len(findings)

    # Evidence per finding (proof embedded under each finding in the report).
    ev_rows = (await db.execute(
        select(Evidence).where(Evidence.project_id == project_id, Evidence.deleted_at.is_(None))
    )).scalars().all()
    ev_map: dict[int, list] = {}
    for ev in ev_rows:
        if ev.finding_id:
            ev_map.setdefault(ev.finding_id, []).append(ev)

    content = _generate_markdown_report(project, findings, rollup, ev_map, include_logs, include_files)

    timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    # Sanitize the project name so it can't traverse out of REPORTS_PATH.
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in (project.name or "project"))[:60]
    base = f"report_{safe_name}_{timestamp}"
    report_path = settings.REPORTS_PATH / f"{base}.md"
    await asyncio.to_thread(_write_report_sync, report_path, content)

    if format == "pdf":
        pdf_path = settings.REPORTS_PATH / f"{base}.pdf"
        try:
            await asyncio.to_thread(_render_pdf_sync, content, pdf_path)
            return FileResponse(pdf_path, filename=pdf_path.name, media_type="application/pdf")
        except (ImportError, OSError) as e:
            # OSError: WeasyPrint imports but its native libs (pango/harfbuzz) are missing.
            raise HTTPException(
                status_code=503,
                detail=f"PDF generation requires WeasyPrint and its system libraries ({e}). Use format=markdown.",
            )

    if format == "html":
        html_path = settings.REPORTS_PATH / f"{base}.html"
        html = _markdown_to_html(content)
        await asyncio.to_thread(_write_report_sync, html_path, html)
        return FileResponse(html_path, filename=html_path.name, media_type="text/html")

    return FileResponse(report_path, filename=report_path.name, media_type="text/markdown")


@router.get("/list")
async def list_reports():
    """List generated reports."""
    reports = await asyncio.to_thread(_list_reports_sync, settings.REPORTS_PATH)
    return {"reports": reports}


@router.get("/download/{filename}")
async def download_report(filename: str):
    """Download a generated report."""
    report_path = settings.REPORTS_PATH / filename
    exists = await asyncio.to_thread(report_path.exists)
    if not exists:
        raise HTTPException(status_code=404, detail="Report not found")

    media_type = "application/pdf" if filename.endswith(".pdf") else "text/markdown"
    return FileResponse(report_path, filename=filename, media_type=media_type)


_SEV_LABEL = {"critical": "Critical", "high": "High", "medium": "Medium", "low": "Low", "info": "Informational"}
_SEV_ORDER_LIST = ["critical", "high", "medium", "low", "info"]


def _exec_summary(project, rollup) -> str:
    """Auto-generate an executive summary sentence from the severity rollup."""
    total = rollup.get("total", 0)
    if total == 0:
        return (f"The assessment of {project.name} did not identify any confirmed findings at the "
                f"time of reporting. Continued monitoring and periodic re-assessment are recommended.")
    parts = [f"{rollup[s]} {_SEV_LABEL[s].lower()}" for s in _SEV_ORDER_LIST if rollup.get(s)]
    breakdown = ", ".join(parts)
    urgent = rollup.get("critical", 0) + rollup.get("high", 0)
    tail = ""
    if urgent:
        tail = (f" **{urgent}** of these are rated Critical or High and warrant prompt remediation, "
                f"as they present a direct path to compromise of the environment.")
    return (f"The penetration test of {project.name} identified **{total}** findings ({breakdown}).{tail}")


def _generate_markdown_report(project: Project, findings, rollup, ev_map, include_logs: bool, include_files: bool) -> str:
    """Generate findings-driven markdown report content."""
    ev_map = ev_map or {}
    host_label = {}
    for h in project.hosts:
        host_label[h.id] = h.display_name or h.ip_address or h.hostname or f"host #{h.id}"

    lines = [
        f"# Penetration Test Report: {project.name}",
        "",
        f"**Generated:** {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S UTC')}",
        f"**Project Created:** {project.created_at.strftime('%Y-%m-%d')}",
        f"**Status:** {project.status.value}",
        "",
        "---",
        "",
        "## Executive Summary",
        "",
        _exec_summary(project, rollup),
        "",
        "### Findings by Severity",
        "",
        "| Critical | High | Medium | Low | Info | Total |",
        "|----------|------|--------|-----|------|-------|",
        (f"| {rollup.get('critical',0)} | {rollup.get('high',0)} | {rollup.get('medium',0)} "
         f"| {rollup.get('low',0)} | {rollup.get('info',0)} | {rollup.get('total',0)} |"),
        "",
        "---",
        "",
        "## Scope",
        "",
    ]

    scope = project.scope or {}
    for key, heading in [("ips", "IP Addresses"), ("cidrs", "Network Ranges"),
                         ("fqdns", "Domains"), ("hostnames", "Hostnames")]:
        if scope.get(key):
            lines.append(f"### {heading}")
            lines.extend(f"- {v}" for v in scope[key])
            lines.append("")
    if scope.get("exclusions"):
        lines.append("### Exclusions (Out of Scope)")
        lines.extend(f"- {v}" for v in scope["exclusions"])
        lines.append("")

    # ---- Findings ----
    lines.extend(["---", "", "## Findings", ""])
    if not findings:
        lines.extend(["_No findings recorded._", ""])
    else:
        for f in findings:
            sv = f.severity.value if f.severity else "info"
            lines.append(f"### [{_SEV_LABEL.get(sv, sv)}] {f.title}")
            lines.append("")
            meta = [f"**Severity:** {_SEV_LABEL.get(sv, sv)}", f"**Status:** {f.status.value if f.status else 'open'}"]
            if f.host_id and f.host_id in host_label:
                meta.append(f"**Affected:** {host_label[f.host_id]}")
            if f.cvss_score:
                meta.append(f"**CVSS:** {f.cvss_score}")
            if f.cwe:
                meta.append(f"**{f.cwe}**")
            if f.cve:
                meta.append(f"**{f.cve}**")
            if f.occurrences and f.occurrences > 1:
                meta.append(f"**Occurrences:** {f.occurrences}")
            lines.append(" · ".join(meta))
            lines.append("")
            if f.description:
                lines.extend([f.description, ""])
            if f.evidence_text:
                lines.extend(["**Evidence:**", "", "```", f.evidence_text.strip(), "```", ""])
            if f.remediation:
                lines.extend(["**Remediation:**", "", f.remediation, ""])
            if f.references:
                lines.append("**References:** " + ", ".join(str(r) for r in f.references))
                lines.append("")
            evs = ev_map.get(f.id) or []
            if evs:
                lines.append("**Evidence Attachments:**")
                lines.append("")
                for ev in evs:
                    if ev.kind in ("screenshot",) and ev.local_path:
                        lines.append(f"- 🖼️ {ev.caption or ev.filename or 'screenshot'} (`{ev.filename}`)")
                    elif ev.content:
                        lines.append(f"- {ev.caption or 'excerpt'}:")
                        lines.extend(["", "```", ev.content.strip()[:2000], "```"])
                    else:
                        lines.append(f"- 📎 {ev.caption or ev.filename or 'attachment'}")
                lines.append("")
            lines.append("")

    lines.extend(["---", "", "## Discovered Hosts", ""])
    
    for host in project.hosts:
        lines.append(f"### {host.display_name}")
        lines.append("")
        if host.ip_address:
            lines.append(f"- **IP:** {host.ip_address}")
        if host.hostname:
            lines.append(f"- **Hostname:** {host.hostname}")
        if host.os_info:
            lines.append(f"- **OS:** {host.os_info}")
        lines.append(f"- **Status:** {host.status}")
        lines.append("")
        
        if host.services:
            lines.append("#### Services")
            lines.append("")
            lines.append("| Port | Protocol | Service | Version |")
            lines.append("|------|----------|---------|---------|")
            for svc in host.services:
                lines.append(f"| {svc.port} | {svc.protocol} | {svc.name or '-'} | {svc.version or '-'} |")
            lines.append("")
        
        if include_logs and host.executions:
            lines.append("#### Execution History")
            lines.append("")
            for exec in sorted(host.executions, key=lambda e: e.created_at):
                status = "✅" if exec.is_success else "❌"
                lines.append(f"- {status} **{exec.command[:50]}...** (v{exec.version})")
                if exec.alerts_triggered:
                    for alert in exec.alerts_triggered:
                        lines.append(f"  - ⚠️ Alert: {alert.get('matched_text', 'Unknown')}")
            lines.append("")
    
    lines.extend([
        "---",
        "",
        "## Recommendations",
        "",
        ("Per-finding remediation guidance is provided inline in the **Findings** section above, "
         "prioritized by severity. Address Critical and High findings first."
         if findings else "No remediation actions required based on current findings."),
        "",
        "---",
        "",
        f"*Report generated by Bars v{settings.APP_VERSION}*",
    ])

    return "\n".join(lines)


def _markdown_to_html(markdown_content: str) -> str:
    """Convert markdown to HTML for PDF generation."""
    try:
        import markdown
        html = markdown.markdown(markdown_content, extensions=['tables', 'fenced_code'])
        return f"""
        <!DOCTYPE html>
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 40px; }}
                table {{ border-collapse: collapse; width: 100%; }}
                th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
                th {{ background-color: #4a5568; color: white; }}
                h1 {{ color: #2d3748; }}
                h2 {{ color: #4a5568; border-bottom: 2px solid #e2e8f0; }}
                code {{ background: #f7fafc; padding: 2px 6px; }}
            </style>
        </head>
        <body>{html}</body>
        </html>
        """
    except ImportError:
        return f"<html><body><pre>{markdown_content}</pre></body></html>"
