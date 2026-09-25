"""Default WS channel filters project-scoped events by membership (locked mode)."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch

from fastapi import WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.execution import Execution, ExecutionStatus
from app.models.project import Project
from app.models.user import ProjectMember, User


class _FakeWS:
    def __init__(self):
        self.sent = []
        self.closed = None

    async def accept(self):
        pass

    async def send_json(self, message):
        self.sent.append(message)

    async def receive_text(self):
        raise WebSocketDisconnect()

    async def close(self, code=1000):
        self.closed = code


async def _mk_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(Project(name="P"))
        await db.flush()
        p = (await db.execute(select(Project))).scalar_one()
        db.add(ChecklistGroup(project_id=p.id, name="G"))
        await db.flush()
        g = (await db.execute(select(ChecklistGroup))).scalar_one()
        db.add(ChecklistItem(group_id=g.id, name="s0", command_template="true"))
        db.add(User(username="op", email="op@x", password_hash="x", is_active=True))
        db.add(User(username="out", email="out@x", password_hash="x", is_active=True))
        await db.flush()
        op = (await db.execute(select(User).where(User.username == "op"))).scalar_one()
        out = (await db.execute(select(User).where(User.username == "out"))).scalar_one()
        item = (await db.execute(select(ChecklistItem))).scalar_one()
        db.add(ProjectMember(project_id=p.id, user_id=op.id))
        db.add(Execution(public_id="ex-1", item_id=item.id, command="true",
                         status=ExecutionStatus.RUNNING))
        await db.commit()
        ex = (await db.execute(select(Execution))).scalar_one()
        return engine, maker, p.id, op.id, out.id, ex.id


async def _run_case(require_auth: bool):
    from app.config import settings
    from app.websocket import manager, notification_listener

    engine, maker, pid, op_id, out_id, ex_id = await _mk_db()
    old = settings.WS_REQUIRE_AUTH
    settings.WS_REQUIRE_AUTH = require_auth
    ws_op, ws_out = _FakeWS(), _FakeWS()
    try:
        await manager.connect(ws_op, "default")
        await manager.connect(ws_out, "default")
        manager.set_user(ws_op, op_id if require_auth else None)
        manager.set_user(ws_out, out_id if require_auth else None)
        with patch("app.database.async_session_maker", maker):
            # execution-scoped event (project resolved via execution lookup)
            await notification_listener("execution_status", {
                "execution_id": ex_id, "status": "completed",
                "item_id": None, "details": {},
            })
            # project-scoped event (project_id direct in payload)
            await notification_listener("variable_updated", {
                "project_id": pid, "data": {"key": "k"}, "timestamp": "t",
            })
        kinds_op = [m["type"] for m in ws_op.sent]
        kinds_out = [m["type"] for m in ws_out.sent]
        return kinds_op, kinds_out
    finally:
        await manager.disconnect(ws_op, "default")
        await manager.disconnect(ws_out, "default")
        settings.WS_REQUIRE_AUTH = old
        await engine.dispose()


def test_default_channel_filtered_in_locked_mode():
    kinds_op, kinds_out = asyncio.run(_run_case(True))
    assert "execution_status" in kinds_op, f"member missed event: {kinds_op}"
    assert "variable_updated" in kinds_op, f"member missed event: {kinds_op}"
    assert "execution_status" not in kinds_out, f"outsider leaked execution_status: {kinds_out}"
    assert "variable_updated" not in kinds_out, f"outsider leaked project event: {kinds_out}"


def test_default_channel_open_mode_unchanged():
    kinds_op, kinds_out = asyncio.run(_run_case(False))
    assert "execution_status" in kinds_op and "execution_status" in kinds_out
    assert "variable_updated" in kinds_op and "variable_updated" in kinds_out


def test_handle_websocket_records_user():
    from unittest.mock import patch as mock_patch
    from app.config import settings
    from app.core.auth import create_access_token

    async def _run():
        from app.websocket import handle_websocket, manager
        engine, maker, pid, op_id, out_id, ex_id = await _mk_db()
        old = settings.WS_REQUIRE_AUTH
        settings.WS_REQUIRE_AUTH = True
        try:
            async with maker() as db:
                op = (await db.execute(select(User).where(User.username == "op"))).scalar_one()
                token = create_access_token(op)

            class _TokenWS(_FakeWS):
                def __init__(self, token):
                    super().__init__()
                    self.headers = {"Authorization": f"Bearer {token}"}
                    self.query_params = {}

            ws = _TokenWS(token)
            seen = {}
            orig = manager.set_user
            with mock_patch("app.database.async_session_maker", maker):
                with mock_patch.object(manager, "set_user",
                                       side_effect=lambda w, u: seen.update({id(w): u}) or orig(w, u)):
                    await handle_websocket(ws, "default")
            assert seen.get(id(ws)) == op_id, f"user not recorded: {seen}"
        finally:
            settings.WS_REQUIRE_AUTH = old
            await engine.dispose()

    asyncio.run(_run())


if __name__ == "__main__":
    test_default_channel_filtered_in_locked_mode()
    test_default_channel_open_mode_unchanged()
    test_handle_websocket_records_user()
