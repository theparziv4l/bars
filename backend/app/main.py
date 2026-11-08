"""FastAPI application entry point."""
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from app.config import settings
from app.database import init_db, close_db
from app.api import api_router
from app.websocket import handle_websocket, setup_websocket_listener
from app.core.sync_worker import sync_worker
from app.core.utils import utcnow

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)


async def _cleanup_orphan_executions(_session_maker=None):
    """Cancel locally-originated PENDING/RUNNING executions and live flow runs
    orphaned by a previous crash.

    PENDING rows are swept too: nothing relaunches them after a restart, so
    they would block their item/host with 409 forever. Executions synced from
    peer nodes are left alone (their live state belongs to the peer). Each
    swept execution records an `executions/update` sync event so peers
    converge instead of split-braining on stale rows. FlowExecutions still
    pending/running are cancelled; paused ones are a legitimate resting state.
    """
    from sqlalchemy import select
    from app.database import async_session_maker
    from app.models.execution import Execution, ExecutionStatus
    from app.models.flow_execution import FlowExecution
    from app.models.sync import SyncEvent

    maker = _session_maker or async_session_maker
    now = utcnow()
    async with maker() as db:
        flows = (await db.execute(
            select(FlowExecution).where(FlowExecution.status.in_(("pending", "running")))
        )).scalars().all()
        for fe in flows:
            fe.status = "cancelled"
            fe.completed_at = now
            fe.error = (fe.error or "") + "Cancelled: application restarted while flow was active."
        if flows:
            await db.commit()
            logger.info(f"[Startup] Cancelled {len(flows)} orphan flow execution(s).")

        result = await db.execute(
            select(Execution).where(
                Execution.status.in_((ExecutionStatus.PENDING, ExecutionStatus.RUNNING))
            )
        )
        candidates = result.scalars().all()
        if not candidates:
            return

        # Origin: the earliest sync event of a peer-applied execution carries
        # the peer's node id (executions have no origin column). No event at
        # all means it never left this node.
        first_source: dict[str, str] = {}
        ev = await db.execute(
            select(SyncEvent.entity_public_id, SyncEvent.source_node_id)
            .where(
                SyncEvent.entity_type == "executions",
                SyncEvent.entity_public_id.in_([e.public_id for e in candidates]),
            )
            .order_by(SyncEvent.id)
        )
        for public_id, source in ev.all():
            first_source.setdefault(public_id, source)
        orphans = [
            e for e in candidates
            if first_source.get(e.public_id, settings.NODE_ID) == settings.NODE_ID
        ]
        if not orphans:
            return

        for ex in orphans:
            ex.status = ExecutionStatus.CANCELLED
            ex.completed_at = now
            ex.updated_at = now
            ex.stderr = (ex.stderr or "") + "\nCancelled: application restarted while execution was active."

        await db.commit()
        for ex in orphans:
            try:
                from app.core.sync import execution_sync_payload, sync_service
                payload = await execution_sync_payload(db, ex)
                await sync_service.record_event(
                    db,
                    entity_type="executions",
                    entity_public_id=ex.public_id,
                    operation="update",
                    payload=payload,
                )
            except Exception as e:
                logger.error(f"[Startup] Sync record (orphan sweep) failed for {ex.public_id}: {e}")
        await db.commit()
        logger.info(f"[Startup] Cleaned up {len(orphans)} orphan execution(s) from previous run.")


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
            logger.warning(f"Warning: Failed to create Pentests directory on host: {result['stderr']}")
    except Exception as e:
        logger.warning(f"Warning: Could not reach host agent to create Pentests directory: {e}")

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

def cors_config() -> dict:
    """CORS options from settings. Credentials are only enabled with explicit
    origins — a wildcard origin with credentials would leak bearer tokens."""
    origins = [o.strip() for o in settings.CORS_ALLOW_ORIGINS.split(",") if o.strip()]
    return {
        "allow_origins": origins,
        "allow_credentials": bool(origins),
        "allow_methods": ["*"],
        "allow_headers": ["*"],
    }


# CORS middleware
app.add_middleware(CORSMiddleware, **cors_config())

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
