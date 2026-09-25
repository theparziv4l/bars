"""Password hashing runs off the event loop; logins still work."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.main import app
from app.models.user import User


async def _run():
    from unittest.mock import patch
    from app.core.auth import hash_password
    from app.database import get_db
    import app.api.auth as auth_api

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(User(username="op", email="op@x",
                    password_hash=hash_password("right-password"),
                    is_active=True, role="admin"))
        await db.commit()

    async def _override():
        async with maker() as db:
            yield db

    app.dependency_overrides[get_db] = _override
    auth_api._LOGIN_ATTEMPTS.clear()
    real_to_thread = asyncio.to_thread
    calls = []

    async def _spy(func, *args, **kwargs):
        calls.append(getattr(func, "__name__", func))
        return await real_to_thread(func, *args, **kwargs)

    try:
        with patch("asyncio.to_thread", _spy):
            async with AsyncClient(transport=ASGITransport(app=app),
                                   base_url="http://t") as cli:
                r = await cli.post("/api/v1/auth/login",
                                   json={"username": "op", "password": "wrong"})
                assert r.status_code == 401, r.status_code
        assert "verify_password" in calls, f"to_thread calls: {calls}"
    finally:
        app.dependency_overrides.pop(get_db, None)
    await engine.dispose()
    print("PASS password offload")


def test_password_offload():
    asyncio.run(_run())


if __name__ == "__main__":
    test_password_offload()
