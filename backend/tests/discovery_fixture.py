"""Shared helpers for discovery tests (not collected: no test_* name)."""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.database import Base
import app.models  # noqa: F401
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.execution import Execution, ExecutionStatus
from app.models.host import Host
from app.models.project import Project


async def make_engine(tmp_path, unique_host_index=False):
    # File DB (not :memory:) so a second session gets its own connection, like prod.
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/d.db")

    @event.listens_for(engine.sync_engine, "connect")
    def _pragmas(conn, rec):
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if unique_host_index:
            await conn.execute(text(
                "CREATE UNIQUE INDEX uq_hosts_project_ip_live ON hosts(project_id, ip_address) "
                "WHERE deleted_at IS NULL"
            ))
    return engine


def session(engine) -> AsyncSession:
    """Session configured like app.database.async_session_maker."""
    return AsyncSession(engine, expire_on_commit=False, autoflush=False)


async def seed(db: AsyncSession, command: str, stdout: str, host_ip=None, host_kw=None):
    """Seed project/group/item/(host)/execution. Returns (project, host, execution)."""
    proj = Project(name=f"P-{uuid.uuid4().hex[:6]}")
    db.add(proj)
    await db.flush()
    grp = ChecklistGroup(project_id=proj.id, name="G")
    db.add(grp)
    await db.flush()
    item = ChecklistItem(group_id=grp.id, name="I", command_template=command)
    db.add(item)
    host = None
    if host_ip:
        host = Host(project_id=proj.id, ip_address=host_ip, **(host_kw or {}))
        db.add(host)
    await db.flush()
    ex = Execution(
        public_id=f"ex-{uuid.uuid4().hex[:8]}",
        item_id=item.id,
        host_id=host.id if host else None,
        command=command,
        stdout=stdout,
        status=ExecutionStatus.COMPLETED,
    )
    db.add(ex)
    await db.commit()
    return proj, host, ex


class Recorder:
    """Stand-in for notification_manager.send_project_update."""

    def __init__(self):
        self.calls = []

    async def __call__(self, *a, **k):
        self.calls.append((a, k))
