"""Deferred push events are reported so the sender does not advance past them."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.core.sync import sync_service
from app.core.sync_worker import _capped_push_cursor


async def _run():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with Session() as db:
        # Service whose host never arrives in this batch -> deferred.
        events = [{
            "event_id": "svc-orphan", "entity_type": "services",
            "entity_public_id": "svc-9", "operation": "create",
            "payload": {"public_id": "svc-9", "host_public_id": "ghost", "port": 22},
            "logical_ts": 50, "source_node_id": "node-a",
        }]
        result = await sync_service.ingest_remote_events(db, peer_node_id="peer", events=events)
        await db.commit()
        assert result.min_deferred_ts == 50, f"deferral must be reported, got {result.min_deferred_ts}"
    await engine.dispose()
    assert _capped_push_cursor(100, 50) == 49
    assert _capped_push_cursor(100, None) == 100
    print("PASS push deferral")


def test_push_deferral_reported_and_capped():
    asyncio.run(_run())


if __name__ == "__main__":
    test_push_deferral_reported_and_capped()
