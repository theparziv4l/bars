"""Extraction errors are not injected into variables; bad regex only loses its key;
variable-updated notification is deferred until commit."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.database import Base
import app.models  # noqa: F401
from app.core.parser import output_parser
from app.core.variable_injection import process_variable_injection
from app.core import variable_injection as vi
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.execution import Execution, ExecutionStatus
from app.models.project import Project
from app.models.variable import ProjectVariable


def test_cmd_failure_is_error_result():
    async def ex(cmd, cwd):
        return {"exit_code": 1, "stdout": "", "stderr": "boom"}
    r = asyncio.run(output_parser.parse_output("", {"k": "cmd:false"}, command_executor=ex))
    assert r["k"].data_type == "error"
    assert r["k"].value.startswith("Extraction Failed")


def test_invalid_regex_only_loses_its_key():
    r = asyncio.run(output_parser.parse_output(
        "port 80 open", {"bad": "([", "good": r"port (\d+)"}, mode="first"))
    assert r["bad"].data_type == "error"
    assert r["good"].value == "80"


def test_ansi_stripped_before_user_regex():
    r = asyncio.run(output_parser.parse_output(
        "\x1b[32mport 80\x1b[0m", {"p": r"port (\d+)$"}, mode="first"))
    assert r["p"].value == "80"


def test_ls_listing_accepts_selinux_suffix():
    out = "-rw-r--r--.  1 user group    123 Jan 20 10:00 a.txt\n"
    assert [e["name"] for e in output_parser.parse_directory_listing(out, "ls")] == ["a.txt"]


class _R:
    def __init__(self, value, data_type="string"):
        self.value = value
        self.data_type = data_type


async def _inject():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    calls = []

    async def fake_send(type, project_id, data):
        calls.append((type, data["key"]))

    orig = vi.notification_manager.send_project_update
    vi.notification_manager.send_project_update = fake_send
    try:
        async with maker() as db:
            proj = Project(name="P"); db.add(proj); await db.flush()
            grp = ChecklistGroup(project_id=proj.id, name="G"); db.add(grp); await db.flush()
            item = ChecklistItem(group_id=grp.id, name="i", command_template="x",
                                 storage_policy={"variable_mapping": {"a": "good", "b": "bad", "c": "empty"}})
            db.add(item); await db.flush()
            db.add(ProjectVariable(project_id=proj.id, key="bad", value="keep-me"))
            ex = Execution(item_id=item.id, command="x", status=ExecutionStatus.COMPLETED)
            db.add(ex); await db.commit()
            item = (await db.execute(select(ChecklistItem).where(ChecklistItem.id == item.id)
                    .options(selectinload(ChecklistItem.group)))).scalar_one()
            await process_variable_injection(
                db, ex, item,
                {"a": _R("80"), "b": _R("Extraction Failed: x", "error"), "c": _R("")},
                var_locks=[asyncio.Lock()])
            await asyncio.sleep(0.01)
            assert calls == []  # deferred until commit
            await db.commit()
            await asyncio.sleep(0.05)
            assert calls == [("variable_updated", "good")], calls
            vals = {v.key: v.value for v in (await db.execute(select(ProjectVariable))).scalars()}
            assert vals == {"good": "80", "bad": "keep-me"}, vals
    finally:
        vi.notification_manager.send_project_update = orig
    await engine.dispose()


def test_injection_skips_errors_and_defers_notify():
    asyncio.run(_inject())
