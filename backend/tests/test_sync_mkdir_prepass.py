"""Project mkdir happens before the ingest write txn, never inside appliers."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.core.sync import sync_service


async def _run():
    from unittest.mock import patch
    calls = []

    async def fake_mkdir(cmd):
        calls.append(cmd)
        return {"exit_code": 0, "stdout": "", "stderr": ""}

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, expire_on_commit=False)

    # 1) _apply_project alone must not touch host_runner.
    async with Session() as db:
        with patch("app.core.host_runner.host_runner.execute_sync", side_effect=fake_mkdir) as m:
            await sync_service._apply_project(db, payload={"public_id": "p1", "name": "P1"})
            assert m.call_count == 0, "_apply_project must do no mkdir IO"
    assert calls == []

    # 2) ingest mkdirs once per distinct project, before applying.
    async with Session() as db:
        with patch("app.core.host_runner.host_runner.execute_sync", side_effect=fake_mkdir):
            await sync_service.ingest_remote_events(db, peer_node_id="peer", events=[
                {"event_id": "e1", "entity_type": "projects", "entity_public_id": "p1",
                 "operation": "create", "payload": {"public_id": "p1", "name": "P1"},
                 "logical_ts": 1, "source_node_id": "n"},
                {"event_id": "e2", "entity_type": "projects", "entity_public_id": "p1",
                 "operation": "update", "payload": {"public_id": "p1", "name": "P1"},
                 "logical_ts": 2, "source_node_id": "n"},
            ])
            await db.commit()
    assert len(calls) == 1 and "P1" in calls[0], f"one mkdir per project, got {calls}"
    await engine.dispose()
    print("PASS mkdir prepass")


def test_mkdir_before_txn_not_in_applier():
    asyncio.run(_run())


if __name__ == "__main__":
    test_mkdir_before_txn_not_in_applier()
