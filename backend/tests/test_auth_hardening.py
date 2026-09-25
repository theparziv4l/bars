"""Login rate-limited; email conflicts 409; create needs 8-char passwords."""
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


async def _mk_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(User(username="op", email="op@x", password_hash="x", is_active=True,
                    role="admin"))
        await db.commit()
    return engine, maker


async def _run():
    from app.core.auth import create_access_token, hash_password
    from app.database import get_db
    import app.api.auth as auth_api

    engine, maker = await _mk_db()
    async with maker() as db:
        from sqlalchemy import select
        op = (await db.execute(select(User))).scalar_one()
        op.password_hash = hash_password("right-password")
        await db.commit()
        token = create_access_token(op)

    async def _override():
        async with maker() as db:
            yield db

    app.dependency_overrides[get_db] = _override
    auth_api._LOGIN_ATTEMPTS.clear()
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as cli:
            for _ in range(10):
                r = await cli.post("/api/v1/auth/login",
                                   json={"username": "op", "password": "wrong"})
                assert r.status_code == 401, r.status_code
            r = await cli.post("/api/v1/auth/login",
                               json={"username": "op", "password": "wrong"})
            assert r.status_code == 429, f"11th attempt: {r.status_code}"
            assert "retry-after" in {k.lower() for k in r.headers}
            # Other-user creation still works for admin (limiter is login-only).
            r = await cli.post("/api/v1/users",
                               json={"username": "op2", "password": "x",
                                     "email": "op2@x"},
                               headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 422, f"short password: {r.status_code}"
            r = await cli.post("/api/v1/users",
                               json={"username": "op2", "password": "long-enough-1",
                                     "email": "op@x"},
                               headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 409, f"dup email: {r.status_code} {r.text[:120]}"
    finally:
        app.dependency_overrides.pop(get_db, None)
    await engine.dispose()
    print("PASS auth hardening")


def test_auth_hardening():
    asyncio.run(_run())


if __name__ == "__main__":
    test_auth_hardening()
