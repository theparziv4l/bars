"""Project auto-import records sync events, copies finding_template, scopes variable lookup."""
import asyncio
import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.core.project_setup import auto_import_for_project
from app.core.sync import SyncService
from app.models.ad_domain import ADDomain
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.flow import Flow, FlowStep
from app.models.host import Host
from app.models.library_variable import LibraryVariable
from app.models.project import Project
from app.models.sync import SyncEvent
from app.models.variable import ProjectVariable


async def _mk():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _noop(self, events):
    return None


async def _run():
    engine, maker = await _mk()
    async with maker() as db:
        # Library: one default checklist template + flow template + default variable.
        tg = ChecklistGroup(project_id=None, name="Tmpl", is_template=True,
                            is_default_import=True, requires_auth=False)
        db.add(tg); await db.flush()
        ti = ChecklistItem(group_id=tg.id, name="smb-signing", command_template="nxc {target}",
                           finding_template={"title": "SMB signing", "severity": "medium"})
        db.add(ti); await db.flush()
        tf = Flow(project_id=None, name="TFlow", is_template=True,
                  is_default_import=True, requires_auth=False)
        db.add(tf); await db.flush()
        db.add(FlowStep(flow_id=tf.id, checklist_item_id=ti.id, order_index=0))
        db.add(LibraryVariable(key="K", value="v", var_type="string", is_default_import=True))
        proj = Project(public_id="p1", name="P"); db.add(proj); await db.flush()
        # Pre-existing host-scoped var and soft-deleted global var with the SAME key.
        host = Host(project_id=proj.id, ip_address="10.0.0.1"); db.add(host); await db.flush()
        db.add(ProjectVariable(project_id=proj.id, key="K", value="hostval", host_id=host.id))
        await db.flush()

        counts = await auto_import_for_project(db, proj.id, [])
        await db.commit()
        assert counts["variables"] == 1, counts  # global K created despite host-scoped K

        item = (await db.execute(select(ChecklistItem).where(ChecklistItem.group_id != tg.id))).scalar_one()
        assert item.finding_template == {"title": "SMB signing", "severity": "medium"}

        events = (await db.execute(select(SyncEvent).order_by(SyncEvent.id))).scalars().all()
        kinds = [e.entity_type for e in events]
        for k in ("checklist_groups", "checklist_items", "flows", "flow_steps", "project_variables"):
            assert k in kinds, (k, kinds)
        step_ev = next(e for e in events if e.entity_type == "flow_steps")
        assert step_ev.payload["item_public_id"] == item.public_id, "step must point at the CLONED item"
        assert step_ev.payload["project_public_id"] == "p1"
        exported = [
            {"event_id": e.event_id, "entity_type": e.entity_type,
             "entity_public_id": e.entity_public_id, "operation": e.operation,
             "payload": e.payload, "logical_ts": e.logical_ts, "source_node_id": e.source_node_id}
            for e in events
        ]

    # The events must apply cleanly on a fresh peer (no deferral = nothing pins the cursor).
    engine2, maker2 = await _mk()
    peer = SyncService()
    exported.insert(0, {"event_id": "pe", "entity_type": "projects", "entity_public_id": "p1",
                        "operation": "create", "payload": {"public_id": "p1", "name": "P"},
                        "logical_ts": 1, "source_node_id": "other"})
    with patch.object(SyncService, "_mkdir_projects_for_events", _noop):
        async with maker2() as db:
            res = await peer.ingest_remote_events(db, peer_node_id="n", events=exported)
            await db.commit()
            assert res.min_deferred_ts is None, res
            assert (await db.execute(select(FlowStep))).scalars().first() is not None
            assert (await db.execute(select(ChecklistItem))).scalars().first() is not None
    await engine.dispose()
    await engine2.dispose()


def test_project_setup_records_events_and_scopes_lookups():
    asyncio.run(_run())


async def _domain_vars():
    engine, maker = await _mk()
    async with maker() as db:
        db.add(LibraryVariable(key="DK", value="v", var_type="string", is_default_import=True))
        proj = Project(public_id="p2", name="P2"); db.add(proj); await db.flush()
        dom = ADDomain(project_id=proj.id, name="corp.local"); db.add(dom); await db.flush()
        counts = await auto_import_for_project(db, proj.id, [dom.id])
        await db.commit()
        # one global + one domain-scoped copy
        assert counts["variables"] == 2, counts
        evs = (await db.execute(select(SyncEvent).where(
            SyncEvent.entity_type == "project_variables"))).scalars().all()
        assert sorted(bool(e.payload.get("ad_domain_public_id")) for e in evs) == [False, True]
    await engine.dispose()


def test_domain_variables_carry_domain_public_id():
    asyncio.run(_domain_vars())
