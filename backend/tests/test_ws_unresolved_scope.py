"""Locked mode: a scoped event whose project cannot be resolved reaches admins only."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.models.user import User


class _FakeWS:
    def __init__(self):
        self.sent = []

    async def send_json(self, message):
        self.sent.append(message)


async def _run(require_auth=True):
    from app.config import settings
    from app.websocket import manager, notification_listener

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(User(username="adm", email="a@x", password_hash="x", is_active=True, role="admin"))
        db.add(User(username="op", email="o@x", password_hash="x", is_active=True, role="operator"))
        await db.commit()
        adm = (await db.execute(select(User).where(User.username == "adm"))).scalar_one()
        op = (await db.execute(select(User).where(User.username == "op"))).scalar_one()

    old = settings.WS_REQUIRE_AUTH
    settings.WS_REQUIRE_AUTH = require_auth
    ws_adm, ws_op = _FakeWS(), _FakeWS()
    try:
        await manager.connect(ws_adm, "default", accept=False)
        await manager.connect(ws_op, "default", accept=False)
        manager.set_user(ws_adm, adm.id if require_auth else None)
        manager.set_user(ws_op, op.id if require_auth else None)
        with patch("app.database.async_session_maker", maker):
            # execution 9999 does not exist (e.g. row not committed yet): unresolvable
            await notification_listener("execution_status", {
                "execution_id": 9999, "status": "running", "item_name": "secret-scan"})
            # truly unscoped event keeps broadcasting
            await notification_listener("system_notice", {"msg": "hello"})
        return ([m["type"] for m in ws_adm.sent], [m["type"] for m in ws_op.sent])
    finally:
        await manager.disconnect(ws_adm, "default")
        await manager.disconnect(ws_op, "default")
        settings.WS_REQUIRE_AUTH = old
        await engine.dispose()


def test_unresolved_scope_fails_closed_to_admins():
    adm, op = asyncio.run(_run(True))
    assert "execution_status" in adm, adm
    assert "execution_status" not in op, f"unresolved-scope event leaked to operator: {op}"
    assert "system_notice" in adm and "system_notice" in op


def test_open_mode_still_broadcasts():
    adm, op = asyncio.run(_run(False))
    assert "execution_status" in adm and "execution_status" in op
