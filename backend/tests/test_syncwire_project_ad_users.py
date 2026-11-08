"""Sync wiring: project creator membership, ad_domains payloads, event order, seeded admin."""
import asyncio
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from tests.api_audit_fixture import api_env
from app.core.host_runner import host_runner
from app.core.sync import SyncService
from app.database import Base
from app.models.ad_domain import ADDomain
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.project import Project
from app.models.sync import SyncEvent
from app.models.user import ProjectMember, User

API = "/api/v1"


async def _events(maker):
    async with maker() as db:
        return (await db.execute(select(SyncEvent).order_by(SyncEvent.id))).scalars().all()


async def _fake_exec(cmd, *a, **k):
    return {"exit_code": 1, "stdout": "", "stderr": ""}


async def _peer():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return engine, async_sessionmaker(engine, expire_on_commit=False)


def test_create_project_events_order_member_and_domain_payload():
    async def run():
        async with api_env() as env:
            async with env.maker() as db:
                g = ChecklistGroup(project_id=None, name="Tmpl", is_template=True,
                                   is_default_import=True, requires_auth=False)
                db.add(g); await db.flush()
                db.add(ChecklistItem(group_id=g.id, name="i", command_template="true"))
                await db.commit()
            with patch.object(host_runner, "execute_sync", _fake_exec):
                r = await env.cli.post(f"{API}/projects", headers=env.op,
                                       json={"name": "NewP", "domains": ["corp.local"]})
            assert r.status_code == 201, r.text
            evs = await _events(env.maker)
            by_type = {}
            for e in evs:
                by_type.setdefault(e.entity_type, []).append(e)

            proj_ev = by_type["projects"][0]
            # Project event precedes everything auto-import records.
            for t in ("checklist_groups", "checklist_items"):
                assert by_type[t], t
                assert all(proj_ev.id < e.id for e in by_type[t]), t

            # Creator membership event.
            mem = by_type["project_members"][0]
            assert mem.operation == "create"
            p = mem.payload
            assert p["project_public_id"] == proj_ev.entity_public_id
            assert p["deleted_at"] is None and p["public_id"] == mem.entity_public_id
            async with env.maker() as db:
                op = (await db.get(User, env.op_id))
                assert p["user_public_id"] == op.public_id

            # Domain payload carries project_public_id.
            dom = by_type["ad_domains"][0]
            assert dom.payload["project_public_id"] == proj_ev.entity_public_id
            assert dom.payload["name"] == "corp.local"

            # Apply on a peer DB: project, member and domain land.
            engine2, maker2 = await _peer()
            svc = SyncService()
            async with maker2() as db2:
                db2.add(User(public_id=op.public_id, username="op", email="op@x",
                             password_hash="x", is_active=True))
                await db2.flush()
                for e in (proj_ev, mem, dom):
                    await svc._apply_event(db2, entity_type=e.entity_type,
                                           operation=e.operation, payload=e.payload)
                await db2.flush()
                assert (await db2.execute(select(ProjectMember))).scalars().one().public_id == mem.entity_public_id
                d2 = (await db2.execute(select(ADDomain))).scalars().one()
                assert d2.public_id == dom.entity_public_id
            await engine2.dispose()
    asyncio.run(run())


def test_ad_domain_api_events_carry_project_public_id():
    async def run():
        async with api_env() as env:
            async with env.maker() as db:
                p = Project(name="P"); db.add(p); await db.commit()
                pid, ppub = p.id, p.public_id
            await env.member_of(pid)
            r = await env.cli.post(f"{API}/ad-domains", headers=env.op,
                                   json={"project_id": pid, "name": "a.local"})
            assert r.status_code == 201, r.text
            did = r.json()["id"]
            r = await env.cli.put(f"{API}/ad-domains/{did}", headers=env.op,
                                  json={"description": "d"})
            assert r.status_code == 200, r.text
            r = await env.cli.delete(f"{API}/ad-domains/{did}", headers=env.op)
            assert r.status_code == 204, r.text
            evs = [e for e in await _events(env.maker) if e.entity_type == "ad_domains"]
            assert [e.operation for e in evs] == ["create", "update", "delete"]
            for e in evs:
                assert e.payload["project_public_id"] == ppub, e.operation
            assert evs[1].payload["description"] == "d"
            assert evs[2].payload["deleted_at"] is not None

            # Peer: create + delete via the real applier.
            engine2, maker2 = await _peer()
            svc = SyncService()
            async with maker2() as db2:
                db2.add(Project(public_id=ppub, name="P")); await db2.flush()
                for e in evs[:2]:
                    await svc._apply_event(db2, entity_type="ad_domains",
                                           operation=e.operation, payload=e.payload)
                await db2.flush()
                d = (await db2.execute(select(ADDomain))).scalars().one()
                assert d.deleted_at is None and d.description == "d"
                await svc._apply_event(db2, entity_type="ad_domains",
                                       operation="delete", payload=evs[2].payload)
                await db2.flush()
                await db2.refresh(d)
                assert d.deleted_at is not None
            await engine2.dispose()
    asyncio.run(run())


def test_seed_default_admin_records_user_event_once():
    async def run():
        engine, maker = await _peer()
        import app.database as dbmod
        with patch.object(dbmod, "async_session_maker", maker):
            await dbmod._seed_default_admin()
            await dbmod._seed_default_admin()  # second boot: no new event
        evs = [e for e in await _events(maker) if e.entity_type == "users"]
        assert len(evs) == 1
        e = evs[0]
        assert e.operation == "create"
        for k in ("public_id", "username", "email", "password_hash", "is_active",
                  "role", "must_change_password", "updated_at"):
            assert k in e.payload, k
        assert e.payload["username"] == "admin" and e.payload["role"] == "admin"
        async with maker() as db:
            admin = (await db.execute(select(User))).scalars().one()
            assert e.entity_public_id == admin.public_id == e.payload["public_id"]
        await engine.dispose()
    asyncio.run(run())
