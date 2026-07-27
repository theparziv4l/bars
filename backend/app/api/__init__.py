"""API routes package."""
from fastapi import APIRouter

from app.api import auth, users, projects, hosts, checklists, executions, flows, files, search, reports, variables, sync, library, ad_domains, findings, evidence

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["Auth"])
api_router.include_router(users.router, prefix="/users", tags=["Users"])
api_router.include_router(projects.router, prefix="/projects", tags=["Projects"])
api_router.include_router(hosts.router, prefix="/hosts", tags=["Hosts"])
api_router.include_router(checklists.router, prefix="/checklists", tags=["Checklists"])
api_router.include_router(executions.router, prefix="/executions", tags=["Executions"])
api_router.include_router(flows.router, prefix="/flows", tags=["Flows"])
api_router.include_router(files.router, prefix="/files", tags=["Files"])
api_router.include_router(search.router, prefix="/search", tags=["Search"])
api_router.include_router(reports.router, prefix="/reports", tags=["Reports"])
api_router.include_router(variables.router, prefix="/variables", tags=["Variables"])
api_router.include_router(sync.router, prefix="/sync", tags=["Sync"])
api_router.include_router(library.router, prefix="/library", tags=["Library"])
api_router.include_router(ad_domains.router, prefix="/ad-domains", tags=["AD Domains"])
api_router.include_router(findings.router, prefix="/findings", tags=["Findings"])
api_router.include_router(evidence.router, prefix="/evidence", tags=["Evidence"])

