"""Collaboration: duplicate-active-execution check never 500s; claim scope key is peer-stable."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.core.collaboration import (
    DuplicateExecutionError, claim_item_scope, ensure_no_active_execution,
    release_item_scope)
from app.core.sync import SyncService
from app.core.utils import utcnow
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.checklist_claim import ChecklistClaim
from app.models.execution import Execution, ExecutionStatus
from app.models.host import Host
from app.models.project import Project
from app.models.user import ProjectMember, User


async def _mk():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _seed(db):
    proj = Project(public_id="proj-1", name="P"); db.add(proj); await db.flush()
    grp = ChecklistGroup(project_id=proj.id, name="G"); db.add(grp); await db.flush()
    item = ChecklistItem(group_id=grp.id, name="i", command_template="true")
    host = Host(public_id="host-pub-1", project_id=proj.id, ip_address="10.0.0.1")
    user = User(username="op", email="op@x", password_hash="x", is_active=True)
    db.add_all([item, host, user]); await db.flush()
    db.add(ProjectMember(project_id=proj.id, user_id=user.id))
    await db.flush()
    return proj, item, host, user


async def _duplicates():
    engine, maker = await _mk()
    async with maker() as db:
        proj, item, host, user = await _seed(db)
        # Two live active rows (already-corrupt state) + one soft-deleted one.
        db.add_all([
            Execution(item_id=item.id, host_id=host.id, command="a", status=ExecutionStatus.PENDING),
            Execution(item_id=item.id, host_id=host.id, command="b", status=ExecutionStatus.RUNNING),
        ])
        await db.flush()
        try:
            await ensure_no_active_execution(db, item_id=item.id, host_id=host.id)
            raise AssertionError("expected DuplicateExecutionError")
        except DuplicateExecutionError:
            pass
        # Soft-deleted active rows must not block.
        for e in (await db.execute(select(Execution))).scalars().all():
            e.deleted_at = utcnow()
        await db.flush()
        await ensure_no_active_execution(db, item_id=item.id, host_id=host.id)
    await engine.dispose()


def test_duplicate_check_tolerates_multiple_rows_and_soft_delete():
    asyncio.run(_duplicates())


async def _scope_key():
    engine, maker = await _mk()
    async with maker() as db:
        proj, item, host, user = await _seed(db)
        claim = await claim_item_scope(db, item_id=item.id, host_id=host.id, user=user)
        assert claim.host_scope_key == f"host:{host.public_id}", claim.host_scope_key
        await db.commit()
        # Release finds it by the same scope.
        rel = await release_item_scope(db, item_id=item.id, host_id=host.id, user=user)
        assert rel is not None and rel.id == claim.id
        await db.commit()

        # Legacy row keyed by local int id is adopted (key upgraded), not duplicated.
        row = (await db.execute(select(ChecklistClaim))).scalar_one()
        row.host_scope_key = f"host:{host.id}"
        await db.commit()
        again = await claim_item_scope(db, item_id=item.id, host_id=host.id, user=user)
        await db.commit()
        rows = (await db.execute(select(ChecklistClaim))).scalars().all()
        assert len(rows) == 1 and again.host_scope_key == f"host:{host.public_id}"
    await engine.dispose()


def test_claim_scope_key_uses_host_public_id():
    asyncio.run(_scope_key())


async def _applier_maps_legacy():
    engine, maker = await _mk()
    async with maker() as db:
        proj, item, host, user = await _seed(db)
        svc = SyncService()
        payload = {
            "public_id": "claim-1", "project_public_id": "proj-1",
            "item_public_id": item.public_id, "host_public_id": host.public_id,
            "host_scope_key": "host:999",  # sender's local int id: meaningless here
            "claimed_by_user_public_id": user.public_id, "is_active": True, "version": 1,
        }
        await svc._apply_checklist_claim(db, operation="claim", payload=payload)
        await db.flush()
        row = (await db.execute(select(ChecklistClaim))).scalar_one()
        assert row.host_scope_key == f"host:{host.public_id}", row.host_scope_key
        assert row.host_id == host.id
    await engine.dispose()


def test_claim_applier_maps_legacy_int_scope_key():
    asyncio.run(_applier_maps_legacy())
