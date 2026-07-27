import uuid
from datetime import datetime, timedelta
import os
import sys
import asyncio

import pytest
from sqlalchemy import delete, select

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

TEST_BASE = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "storage", "tmp-tests"))
os.environ.setdefault("STORAGE_PATH", os.path.join(TEST_BASE, "storage"))
os.environ.setdefault("REPORTS_PATH", os.path.join(TEST_BASE, "reports"))
os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{os.path.join(TEST_BASE, 'collab_sync_tests.db')}")

from app.core.collaboration import (
    ClaimConflictError,
    DuplicateExecutionError,
    claim_item_scope,
    ensure_no_active_execution,
)
from app.core.sync import sync_service
from app.database import async_session_maker, init_db
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.checklist_claim import ChecklistClaim
from app.models.execution import Execution, ExecutionStatus
from app.models.project import Project
from app.models.sync import SyncEvent
from app.models.user import ProjectMember, User


def test_claim_conflict_takeover_and_duplicate_prevention():
    async def _run():
        await init_db()
        async with async_session_maker() as db:
            # Cleanup from prior runs
            await db.execute(delete(ChecklistClaim))
            await db.execute(delete(Execution))
            await db.execute(delete(ChecklistItem))
            await db.execute(delete(ChecklistGroup))
            await db.execute(delete(ProjectMember))
            await db.execute(delete(Project))
            await db.execute(delete(User))
            await db.commit()

            # Seed users + project
            op1 = User(
                username=f"op1_{uuid.uuid4().hex[:8]}",
                email=f"op1_{uuid.uuid4().hex[:8]}@local",
                password_hash="hash",
            )
            op2 = User(
                username=f"op2_{uuid.uuid4().hex[:8]}",
                email=f"op2_{uuid.uuid4().hex[:8]}@local",
                password_hash="hash",
            )
            db.add_all([op1, op2])
            await db.flush()

            project = Project(name=f"proj-{uuid.uuid4().hex[:8]}")
            db.add(project)
            await db.flush()

            db.add_all(
                [
                    ProjectMember(project_id=project.id, user_id=op1.id),
                    ProjectMember(project_id=project.id, user_id=op2.id),
                ]
            )

            group = ChecklistGroup(project_id=project.id, name="Recon")
            db.add(group)
            await db.flush()
            item = ChecklistItem(group_id=group.id, name="Nmap", command_template="echo test")
            db.add(item)
            await db.flush()
            await db.commit()

            # op1 claims, op2 cannot claim without takeover
            claim_1 = await claim_item_scope(db, item_id=item.id, host_id=None, user=op1)
            assert claim_1.is_active is True
            with pytest.raises(ClaimConflictError):
                await claim_item_scope(db, item_id=item.id, host_id=None, user=op2, force_takeover=False)

            claim_2 = await claim_item_scope(db, item_id=item.id, host_id=None, user=op2, force_takeover=True)
            assert claim_2.claimed_by_user_id == op2.id

            # Duplicate execution guard
            existing = Execution(
                item_id=item.id,
                host_id=None,
                command="echo test",
                status=ExecutionStatus.PENDING,
                variables_used={},
            )
            db.add(existing)
            await db.flush()

            with pytest.raises(DuplicateExecutionError):
                await ensure_no_active_execution(db, item_id=item.id, host_id=None)

    asyncio.run(_run())


def test_sync_ingest_is_idempotent():
    async def _run():
        await init_db()
        async with async_session_maker() as db:
            await db.execute(delete(SyncEvent))
            await db.execute(delete(ChecklistClaim))
            await db.execute(delete(ChecklistItem))
            await db.execute(delete(ChecklistGroup))
            await db.execute(delete(ProjectMember))
            await db.execute(delete(Project))
            await db.execute(delete(User))
            await db.commit()

            user = User(
                username=f"sync_user_{uuid.uuid4().hex[:8]}",
                email=f"sync_user_{uuid.uuid4().hex[:8]}@local",
                password_hash="hash",
                public_id=str(uuid.uuid4()),
            )
            db.add(user)
            await db.flush()

            project = Project(name=f"sync-proj-{uuid.uuid4().hex[:8]}", public_id=str(uuid.uuid4()))
            db.add(project)
            await db.flush()
            db.add(ProjectMember(project_id=project.id, user_id=user.id, public_id=str(uuid.uuid4())))

            group = ChecklistGroup(project_id=project.id, name="Recon", public_id=str(uuid.uuid4()))
            db.add(group)
            await db.flush()
            item = ChecklistItem(
                group_id=group.id,
                name="Nmap",
                command_template="echo test",
                public_id=str(uuid.uuid4()),
            )
            db.add(item)
            await db.flush()
            await db.commit()

            claim_public_id = str(uuid.uuid4())
            event_payload = {
                "public_id": claim_public_id,
                "project_id": project.id,
                "project_public_id": project.public_id,
                "item_id": item.id,
                "item_public_id": item.public_id,
                "host_id": None,
                "host_scope_key": "global",
                "claimed_by_user_id": user.id,
                "claimed_by_user_public_id": user.public_id,
                "claimed_at": datetime.utcnow().isoformat(),
                "lease_expires_at": (datetime.utcnow() + timedelta(minutes=5)).isoformat(),
                "is_active": True,
                "version": 1,
                "updated_at": datetime.utcnow().isoformat(),
                "deleted_at": None,
            }
            event = {
                "event_id": str(uuid.uuid4()),
                "entity_type": "checklist_claims",
                "entity_public_id": claim_public_id,
                "operation": "claim",
                "payload": event_payload,
                "logical_ts": int(datetime.utcnow().timestamp() * 1_000_000),
                "source_node_id": "peer-node",
            }

            first = await sync_service.ingest_remote_events(db, peer_node_id="peer-node", events=[event])
            second = await sync_service.ingest_remote_events(db, peer_node_id="peer-node", events=[event])
            await db.commit()

            assert first.applied == 1
            assert second.applied == 0
            assert second.skipped >= 1

            count = (await db.execute(select(SyncEvent).where(SyncEvent.event_id == event["event_id"]))).scalars().all()
            assert len(count) == 1

    asyncio.run(_run())
