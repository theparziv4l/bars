"""ad_domains events apply; project-scoped payloads carry project_public_id; extra_data merges."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.core.sync import (
    SyncService, ad_domain_sync_payload, discovered_file_sync_payload,
    execution_sync_payload, flow_step_sync_payload, service_sync_payload)
from app.models.ad_domain import ADDomain
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.execution import Execution
from app.models.file import DiscoveredFile
from app.models.flow import Flow, FlowStep
from app.models.host import Host, Service
from app.models.project import Project


async def _mk():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


async def _ad_domain_roundtrip():
    engine, maker = await _mk()
    svc = SyncService()
    async with maker() as db:
        proj = Project(public_id="p1", name="P"); db.add(proj); await db.flush()
        dom = ADDomain(public_id="d1", project_id=proj.id, name="corp.local", dc_ip="10.0.0.5")
        db.add(dom); await db.flush()
        payload = await ad_domain_sync_payload(db, dom)
        assert payload["project_public_id"] == "p1"

    # Fresh peer: apply create then update then delete.
    engine2, maker2 = await _mk()
    async with maker2() as db:
        db.add(Project(public_id="p1", name="P")); await db.flush()
        await svc._apply_event(db, entity_type="ad_domains", operation="create", payload=payload)
        await db.flush()
        row = (await db.execute(select(ADDomain))).scalar_one()
        assert (row.public_id, row.name, row.dc_ip) == ("d1", "corp.local", "10.0.0.5")
        await svc._apply_event(db, entity_type="ad_domains", operation="update",
                               payload={**payload, "name": "corp2.local"})
        await db.flush()
        row = (await db.execute(select(ADDomain))).scalar_one()
        assert row.name == "corp2.local"
        await svc._apply_event(db, entity_type="ad_domains", operation="delete",
                               payload={"public_id": "d1"})
        await db.flush()
        row = (await db.execute(select(ADDomain))).scalar_one()
        assert row.deleted_at is not None
    await engine.dispose()
    await engine2.dispose()


def test_ad_domain_applier():
    asyncio.run(_ad_domain_roundtrip())


async def _project_public_id_in_payloads():
    engine, maker = await _mk()
    async with maker() as db:
        proj = Project(public_id="p1", name="P"); db.add(proj); await db.flush()
        grp = ChecklistGroup(project_id=proj.id, name="G"); db.add(grp); await db.flush()
        item = ChecklistItem(group_id=grp.id, name="i", command_template="true")
        host = Host(project_id=proj.id, ip_address="10.0.0.1")
        db.add_all([item, host]); await db.flush()
        ex = Execution(item_id=item.id, host_id=host.id, command="x")
        svc_row = Service(host_id=host.id, port=80)
        df = DiscoveredFile(host_id=host.id, share_name="s", path="/", name="n")
        flow = Flow(project_id=proj.id, name="f")
        db.add_all([ex, svc_row, df, flow]); await db.flush()
        step = FlowStep(flow_id=flow.id, checklist_item_id=item.id, order_index=0)
        db.add(step); await db.flush()
        for builder, obj in (
            (execution_sync_payload, ex), (service_sync_payload, svc_row),
            (discovered_file_sync_payload, df), (flow_step_sync_payload, step),
        ):
            p = await builder(db, obj)
            assert p.get("project_public_id") == "p1", (builder.__name__, p.get("project_public_id"))
    await engine.dispose()


def test_scoped_payloads_carry_project_public_id():
    asyncio.run(_project_public_id_in_payloads())


async def _extra_data_merge():
    engine, maker = await _mk()
    svc = SyncService()
    async with maker() as db:
        db.add(Project(
            public_id="p1", name="P", scope={},
            extra_data={"project_path": "/local/P", "smb_credentials": {"user": "mine"}, "k": "old"},
        ))
        await db.flush()
        await svc._apply_event(db, entity_type="projects", operation="update", payload={
            "public_id": "p1", "name": "P",
            "extra_data": {"project_path": "/remote/P", "smb_credentials": {"user": "theirs"},
                           "k": "new", "added": 1},
        })
        await db.flush()
        proj = (await db.execute(select(Project))).scalar_one()
        assert proj.extra_data["project_path"] == "/local/P", proj.extra_data
        assert proj.extra_data["smb_credentials"] == {"user": "mine"}, proj.extra_data
        assert proj.extra_data["k"] == "new" and proj.extra_data["added"] == 1

        # Peer payload lacking extra_data must not wipe local keys either.
        await svc._apply_event(db, entity_type="projects", operation="update",
                               payload={"public_id": "p1", "name": "P"})
        await db.flush()
        await db.refresh(proj)
        assert proj.extra_data["project_path"] == "/local/P"
        assert proj.extra_data["smb_credentials"] == {"user": "mine"}

        # New project (no local state): peer smb creds are adopted, path is local.
        await svc._apply_event(db, entity_type="projects", operation="create", payload={
            "public_id": "p2", "name": "Q",
            "extra_data": {"project_path": "/remote/Q", "smb_credentials": {"user": "theirs"}},
        })
        await db.flush()
        q = (await db.execute(select(Project).where(Project.public_id == "p2"))).scalar_one()
        assert q.extra_data["project_path"] != "/remote/Q"
        assert q.extra_data["smb_credentials"] == {"user": "theirs"}
    await engine.dispose()


def test_project_extra_data_merged_not_replaced():
    asyncio.run(_extra_data_merge())
