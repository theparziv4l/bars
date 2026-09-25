"""browse-local needs auth when required and never interpolates paths."""
import asyncio
import base64
import os
import sys
from unittest.mock import patch, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.main import app
from app.models.user import User


async def _mk_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(User(username="op", email="op@x", password_hash="x", is_active=True))
        await db.commit()
    return engine, maker


async def _run():
    from app.config import settings
    from app.core.auth import create_access_token
    from app.database import get_db

    engine, maker = await _mk_db()

    async def _override():
        async with maker() as db:
            yield db

    app.dependency_overrides[get_db] = _override
    old = settings.AUTH_REQUIRED
    settings.AUTH_REQUIRED = True
    try:
        async with maker() as db:
            from sqlalchemy import select
            user = (await db.execute(select(User))).scalar_one()
            token = create_access_token(user)
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as cli:
            r = await cli.get("/api/v1/files/browse-local", params={"path": "/tmp"})
            assert r.status_code == 401, f"no token: {r.status_code}"
            evil = '/tmp"; $(touch /tmp/pwned) #'
            with patch("app.core.host_runner.host_runner") as hr:
                hr.execute_sync = AsyncMock(return_value={
                    "exit_code": 0, "stdout": '{"current_path": "/tmp"}', "stderr": ""})
                r = await cli.get(
                    "/api/v1/files/browse-local", params={"path": evil},
                    headers={"Authorization": f"Bearer {token}"})
                assert r.status_code == 200, r.text[:200]
                sent = hr.execute_sync.call_args.args[0]
                assert evil not in sent, "raw path interpolated into shell"
                assert "$(" not in sent and "`" not in sent
                assert base64.b64encode(evil.encode()).decode() in sent
    finally:
        settings.AUTH_REQUIRED = old
        app.dependency_overrides.pop(get_db, None)
    await engine.dispose()
    print("PASS browse-local")


def test_browse_local_auth_and_encoding():
    asyncio.run(_run())


if __name__ == "__main__":
    test_browse_local_auth_and_encoding()
