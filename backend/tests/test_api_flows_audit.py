"""Audit 41/55: flow status/context scoping, import templates only, step item scope."""
import asyncio

from sqlalchemy import select, func

from tests.api_audit_fixture import api_env
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.execution import Execution, ExecutionStatus
from app.models.flow import Flow, FlowStep
from app.models.flow_execution import FlowExecution
from app.models.project import Project


async def _seed(env):
    async with env.maker() as db:
        p1, p2 = Project(name="One"), Project(name="Two")
        db.add_all([p1, p2]); await db.flush()
        g1 = ChecklistGroup(project_id=p1.id, name="G1")
        g2 = ChecklistGroup(project_id=p2.id, name="G2")
        gt = ChecklistGroup(project_id=None, name="GT", is_template=True)
        db.add_all([g1, g2, gt]); await db.flush()
        i1 = ChecklistItem(group_id=g1.id, name="i1", command_template="true")
        i2 = ChecklistItem(group_id=g2.id, name="i2", command_template="true")
        it = ChecklistItem(group_id=gt.id, name="it", command_template="true")
        f1 = Flow(project_id=p1.id, name="F1")
        f2 = Flow(project_id=p2.id, name="F2")
        ft = Flow(project_id=None, name="FT", is_template=True)
        db.add_all([i1, i2, it, f1, f2, ft]); await db.flush()
        db.add_all([
            FlowExecution(id="fx1", flow_id=f1.id, status="running"),
            FlowExecution(id="fx2", flow_id=f2.id, status="running"),
            FlowExecution(id="fxt", flow_id=ft.id, status="running"),
        ])
        db.add(Execution(public_id="e-t", item_id=it.id, command="true", status=ExecutionStatus.COMPLETED,
                         flow_execution_id="fxt", parsed_output={"secret": 1}))
        db.add(FlowStep(flow_id=f2.id, checklist_item_id=i2.id, order_index=0))
        await db.commit()
        ids = dict(p1=p1.id, p2=p2.id, i1=i1.id, i2=i2.id, it=it.id, f1=f1.id, f2=f2.id, ft=ft.id)
    await env.member_of(ids["p1"])
    return ids


def test_flow_status_rejects_foreign_run():
    async def run():
        async with api_env() as env:
            ids = await _seed(env)
            r = await env.cli.get(f"/api/v1/flows/{ids['f1']}/status",
                                  headers=env.op, params={"flow_execution_id": "fx2"})
            assert r.status_code == 404, r.text
            r = await env.cli.get(f"/api/v1/flows/{ids['f1']}/status",
                                  headers=env.op, params={"flow_execution_id": "fx1"})
            assert r.status_code == 200, r.text
    asyncio.run(run())


def test_flow_context_fails_closed_without_project():
    async def run():
        async with api_env() as env:
            await _seed(env)
            r = await env.cli.get("/api/v1/flows/execution/fxt/context", headers=env.op)
            assert r.status_code in (403, 404), r.text
            r = await env.cli.get("/api/v1/flows/execution/fx2/context", headers=env.op)
            assert r.status_code == 403, r.text
    asyncio.run(run())


def test_flow_import_copies_templates_only():
    async def run():
        async with api_env() as env:
            ids = await _seed(env)
            r = await env.cli.post("/api/v1/flows/import", headers=env.op,
                                   params={"project_id": ids["p1"]}, json=[ids["f2"], ids["ft"]])
            assert r.status_code == 201, r.text
            assert r.json()["count"] == 1
            async with env.maker() as db:
                names = (await db.execute(select(Flow.name).where(Flow.project_id == ids["p1"]))).scalars().all()
                assert sorted(names) == ["F1", "FT"], names
    asyncio.run(run())


def test_flow_steps_item_scope():
    async def run():
        async with api_env() as env:
            ids = await _seed(env)
            body = lambda item: {"name": "N", "project_id": ids["p1"],
                                 "steps": [{"checklist_item_id": item}]}
            r = await env.cli.post("/api/v1/flows", headers=env.op, json=body(ids["i2"]))
            assert r.status_code == 400, r.text
            for ok in (ids["i1"], ids["it"]):
                r = await env.cli.post("/api/v1/flows", headers=env.op, json=body(ok))
                assert r.status_code == 201, (ok, r.text)
            r = await env.cli.put(f"/api/v1/flows/{ids['f1']}", headers=env.op,
                                  json={"steps": [{"checklist_item_id": ids["i2"]}]})
            assert r.status_code == 400, r.text
            r = await env.cli.put(f"/api/v1/flows/{ids['f1']}", headers=env.op,
                                  json={"steps": [{"checklist_item_id": ids["i1"]}]})
            assert r.status_code == 200, r.text
            r = await env.cli.post("/api/v1/flows", headers=env.op,
                                   json={"name": "N", "project_id": ids["p1"],
                                         "steps": [{"checklist_item_id": 99999}]})
            assert r.status_code == 400, r.text
    asyncio.run(run())
