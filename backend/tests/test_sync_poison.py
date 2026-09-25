"""Poison events are skipped+counted; oversized pushes are rejected by schema."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from pydantic import ValidationError
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.core.sync import sync_service
from app.models.project import Project
from app.schemas.sync import SyncPushRequest


def _good(ts):
    return {
        "event_id": f"good-{ts}", "entity_type": "projects",
        "entity_public_id": f"p-{ts}", "operation": "create",
        "payload": {"public_id": f"p-{ts}", "name": "G"},
        "logical_ts": ts, "source_node_id": "node-a",
    }


async def _run():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        events = [
            _good(1),
            {"entity_type": "projects"},  # missing event_id/logical_ts -> poison
            {"event_id": "x", "entity_type": "projects", "entity_public_id": "q",
             "operation": "create", "payload": {}, "logical_ts": "not-an-int",
             "source_node_id": "n"},  # bad ts type -> poison
            _good(2),
        ]
        result = await sync_service.ingest_remote_events(db, peer_node_id="peer", events=events)
        await db.commit()
        assert result.applied == 2, f"good events must apply, got {result.applied}"
        assert result.skipped >= 2, "poison events must be counted as skipped"
        n = (await db.execute(select(func.count()).select_from(Project))).scalar()
        assert n == 2
    await engine.dispose()
    print("PASS poison skipped")


def test_poison_events_do_not_abort_batch():
    asyncio.run(_run())


def test_push_cap_rejects_oversized_batch():
    ev = {
        "event_id": "e", "entity_type": "projects", "entity_public_id": "p",
        "operation": "create", "payload": {}, "logical_ts": 1,
        "source_node_id": "n", "created_at": "2026-01-01T00:00:00",
    }
    with pytest.raises(ValidationError):
        SyncPushRequest(source_node_id="n", events=[dict(ev, event_id=f"e{i}") for i in range(1001)])


if __name__ == "__main__":
    test_poison_events_do_not_abort_batch()
    test_push_cap_rejects_oversized_batch()
