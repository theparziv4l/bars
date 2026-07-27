"""FastAPI application entry point."""
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db, close_db
from app.api import api_router
from app.websocket import handle_websocket, setup_websocket_listener
from app.core.sync_worker import sync_worker


async def _cleanup_orphan_executions():
    """Mark any PENDING/RUNNING executions as CANCELLED on startup (stale from previous crash)."""
    from datetime import datetime
    from sqlalchemy import select, func
    from app.database import async_session_maker
    from app.models.execution import Execution, ExecutionStatus

    async with async_session_maker() as db:
        result = await db.execute(
            select(Execution).where(
                Execution.status.in_([ExecutionStatus.PENDING, ExecutionStatus.RUNNING])
            )
        )
        orphans = result.scalars().all()
        if not orphans:
            return

        now = datetime.utcnow()
        for ex in orphans:
            ex.status = ExecutionStatus.CANCELLED
            ex.completed_at = now
            ex.stderr = (ex.stderr or "") + "\nCancelled: application restarted while execution was active."

        await db.commit()
        print(f"[Startup] Cleaned up {len(orphans)} orphan execution(s) from previous run.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler."""
    from app.core.host_runner import host_runner

    # Startup
    await init_db()
    await _cleanup_orphan_executions()
    await setup_websocket_listener()

    # Ensure the Pentests base directory exists on the host
    try:
        result = await host_runner.execute_sync(f'mkdir -p "{settings.PROJECTS_BASE_PATH}"')
        if result["exit_code"] != 0:
            print(f"Warning: Failed to create Pentests directory on host: {result['stderr']}")
    except Exception as e:
        print(f"Warning: Could not reach host agent to create Pentests directory: {e}")

    # Initialize Flow Manager (Event Listeners)
    from app.core.flow_manager import flow_manager
    await flow_manager.initialize()
    await sync_worker.start()
    yield
    # Shutdown
    await sync_worker.stop()
    await host_runner.close()
    await close_db()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="Advanced Penetration Testing Orchestration Framework",
    lifespan=lifespan,
    redirect_slashes=False
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API routes
app.include_router(api_router, prefix="/api/v1")


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "status": "operational"
    }


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {"status": "healthy"}


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Main WebSocket endpoint."""
    await handle_websocket(websocket, "default")


@app.websocket("/ws/execution/{execution_id}")
async def execution_websocket(websocket: WebSocket, execution_id: int):
    """WebSocket for specific execution updates."""
    await handle_websocket(websocket, f"execution:{execution_id}")


@app.websocket("/ws/project/{project_id}")
async def project_websocket(websocket: WebSocket, project_id: int):
    """WebSocket for project-wide updates."""
    await handle_websocket(websocket, f"project:{project_id}")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG
    )
