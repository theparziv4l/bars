"""Audit 59: library export/import round-trips variables/target_filter/target_mode
and never binds flow steps to another project's items."""
import asyncio
import json

from sqlalchemy import select

from tests.api_audit_fixture import api_env
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.flow import Flow, FlowStep
from app.models.project import Project


def test_library_roundtrip_keeps_fields():
    async def run():
        async with api_env() as env:
            async with env.maker() as db:
                g = ChecklistGroup(project_id=None, name="G", is_template=True)
                db.add(g); await db.flush()
                it = ChecklistItem(group_id=g.id, name="it", command_template="true",
                                   variables={"a": "1"}, target_filter={"tags": ["smb"]})
                f = Flow(project_id=None, name="F", is_template=True)
                db.add_all([it, f]); await db.flush()
                db.add(FlowStep(flow_id=f.id, checklist_item_id=it.id, order_index=0,
                                target_mode="filtered", target_filter={"tags": ["web"]}))
                await db.commit()
            r = await env.cli.get("/api/v1/library/export", headers=env.op)
            assert r.status_code == 200
            exported = r.json()
            item = exported["checklists"]["groups"][0]["items"][0]
            assert item["variables"] == {"a": "1"} and item["target_filter"] == {"tags": ["smb"]}
            step = exported["flows"]["flows"][0]["steps"][0]
            assert step["target_mode"] == "filtered" and step["target_filter"] == {"tags": ["web"]}
            # wipe and re-import
            async with env.maker() as db:
                for m in (FlowStep, Flow, ChecklistItem, ChecklistGroup):
                    for row in (await db.execute(select(m))).scalars().all():
                        await db.delete(row)
                await db.commit()
            r = await env.cli.post("/api/v1/library/import", headers=env.op,
                                   files={"file": ("l.json", json.dumps(exported), "application/json")})
            assert r.status_code == 200, r.text
            async with env.maker() as db:
                it = (await db.execute(select(ChecklistItem))).scalar_one()
                assert it.variables == {"a": "1"} and it.target_filter == {"tags": ["smb"]}
                st = (await db.execute(select(FlowStep))).scalar_one()
                assert st.target_mode == "filtered" and st.target_filter == {"tags": ["web"]}
    asyncio.run(run())


def test_library_import_does_not_bind_project_items():
    async def run():
        async with api_env() as env:
            async with env.maker() as db:
                p = Project(name="P"); db.add(p); await db.flush()
                g = ChecklistGroup(project_id=p.id, name="PG"); db.add(g); await db.flush()
                db.add(ChecklistItem(group_id=g.id, name="private", command_template="true"))
                await db.commit()
            payload = {"flows": {"flows": [{"name": "F", "steps": [
                {"checklist_item_name": "private", "order_index": 0}]}]}}
            r = await env.cli.post("/api/v1/library/import", headers=env.op,
                                   files={"file": ("l.json", json.dumps(payload), "application/json")})
            assert r.status_code == 200, r.text
            assert r.json().get("warnings")
            async with env.maker() as db:
                assert (await db.execute(select(FlowStep))).scalars().all() == []
    asyncio.run(run())
