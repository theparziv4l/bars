"""Channel subscribe checks project membership for identified users."""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.execution import Execution, ExecutionStatus
from app.models.project import Project
from app.models.user import ProjectMember, User
from app.websocket import handle_websocket, manager


class _FakeWS:
    def __init__(self, incoming, token=None):
        self.sent = []
        self.incoming = list(incoming)
        self.headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.query_params = {}
        self.closed = None

    async def accept(self):
        pass

    async def send_json(self, message):
        self.sent.append(message)

    async def receive_text(self):
        if not self.incoming:
            raise WebSocketDisconnect()
        return self.incoming.pop(0)

    async def close(self, code=1000):
        self.closed = code


async def _run():
    from unittest.mock import patch
    from app.config import settings
    from app.core.auth import create_access_token

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(Project(name="P")); await db.flush()
        p = (await db.execute(select(Project))).scalar_one()
        db.add(ChecklistGroup(project_id=p.id, name="G")); await db.flush()
        g = (await db.execute(select(ChecklistGroup))).scalar_one()
        db.add(ChecklistItem(group_id=g.id, name="s0", command_template="true"))
        db.add(User(username="out", email="o@x", password_hash="x", is_active=True))
        db.add(User(username="op", email="op@x", password_hash="x", is_active=True))
        await db.flush()
        op = (await db.execute(select(User).where(User.username == "op"))).scalar_one()
        out = (await db.execute(select(User).where(User.username == "out"))).scalar_one()
        item = (await db.execute(select(ChecklistItem))).scalar_one()
        db.add(ProjectMember(project_id=p.id, user_id=op.id))
        db.add(Execution(public_id="ex-1", item_id=item.id, command="true",
                         status=ExecutionStatus.RUNNING))
        await db.commit()
        ex = (await db.execute(select(Execution))).scalar_one()
        t_op, t_out = create_access_token(op), create_access_token(out)

    old = settings.WS_REQUIRE_AUTH
    settings.WS_REQUIRE_AUTH = True
    try:
        sub = json.dumps({"type": "subscribe", "channel": f"execution:{ex.id}"})
        outsider = _FakeWS([sub], token=t_out)
        with patch("app.database.async_session_maker", maker):
            await handle_websocket(outsider, "default")
        kinds = [m.get("type") for m in outsider.sent]
        assert "error" in kinds, outsider.sent
        async with manager._lock:
            assert outsider not in manager.active_connections.get(f"execution:{ex.id}", set())
        insider = _FakeWS([sub], token=t_op)
        with patch("app.database.async_session_maker", maker):
            await handle_websocket(insider, "default")
        # Member subscription succeeds (no error); cleanup removes it after disconnect.
        assert "error" not in [m.get("type") for m in insider.sent], insider.sent
    finally:
        settings.WS_REQUIRE_AUTH = old
    await engine.dispose()
    print("PASS ws subscribe authz")


def test_ws_subscribe_authz():
    asyncio.run(_run())


if __name__ == "__main__":
    test_ws_subscribe_authz()
