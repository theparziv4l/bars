"""A peer host that collides with a live local host on (project, ip) becomes a tombstone."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.core.sync import SyncService
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.execution import Execution
from app.models.host import Host, Service
from app.models.project import Project
from app.models.variable import ProjectVariable


def _ev(eid, etype, pid, payload, ts):
    return {"event_id": eid, "entity_type": etype, "entity_public_id": pid,
            "operation": "create", "payload": payload, "logical_ts": ts,
            "source_node_id": "peer"}


async def _run():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    svc = SyncService()
    async with maker() as db:
        proj = Project(public_id="p1", name="P"); db.add(proj); await db.flush()
        grp = ChecklistGroup(project_id=proj.id, name="G"); db.add(grp); await db.flush()
        item = ChecklistItem(group_id=grp.id, name="i", command_template="true",
                             public_id="item-1")
        local = Host(public_id="local-h", project_id=proj.id, ip_address="10.0.0.5")
        db.add_all([item, local]); await db.commit()

        events = [
            _ev("e1", "hosts", "peer-h", {
                "public_id": "peer-h", "project_public_id": "p1",
                "ip_address": "10.0.0.5", "hostname": "dc01", "status": "up"}, 10),
            _ev("e2", "services", "peer-s", {
                "public_id": "peer-s", "host_public_id": "peer-h", "port": 445}, 11),
            _ev("e3", "executions", "peer-x", {
                "public_id": "peer-x", "item_public_id": "item-1",
                "host_public_id": "peer-h", "command": "c", "status": "completed"}, 12),
            _ev("e4", "project_variables", "peer-v", {
                "public_id": "peer-v", "project_public_id": "p1", "key": "K",
                "host_public_id": "peer-h", "value": "v"}, 13),
        ]
        result = await svc.ingest_remote_events(db, peer_node_id="peer", events=events)
        await db.commit()
        assert result.applied == 4 and result.min_deferred_ts is None, result

        hosts = {h.public_id: h for h in (await db.execute(select(Host))).scalars().all()}
        assert hosts["local-h"].deleted_at is None
        assert hosts["peer-h"].deleted_at is not None, "colliding peer host must be a tombstone"
        assert hosts["local-h"].hostname == "dc01", "empty local field filled from peer"

        s = (await db.execute(select(Service).where(Service.public_id == "peer-s"))).scalar_one()
        assert s.host_id == hosts["local-h"].id, "service must attach to the live host"
        x = (await db.execute(select(Execution).where(Execution.public_id == "peer-x"))).scalar_one()
        assert x.host_id == hosts["local-h"].id
        v = (await db.execute(select(ProjectVariable).where(ProjectVariable.public_id == "peer-v"))).scalar_one()
        assert v.host_id == hosts["local-h"].id
    await engine.dispose()


def test_peer_host_ip_collision_becomes_tombstone():
    asyncio.run(_run())
