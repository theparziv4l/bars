"""Search: bounded inputs, escaped LIKE, auth + membership."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.main import app
from app.models.project import Project
from app.models.user import ProjectMember, User


async def _mk_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(Project(name="P"))
        db.add(User(username="op", email="op@x", password_hash="x", is_active=True))
        db.add(User(username="out", email="out@x", password_hash="x", is_active=True))
        await db.flush()
        from sqlalchemy import select
        p = (await db.execute(select(Project))).scalar_one()
        op = (await db.execute(select(User).where(User.username == "op"))).scalar_one()
        db.add(ProjectMember(project_id=p.id, user_id=op.id))
        await db.commit()
        return engine, maker, p.id


async def _run():
    from app.config import settings
    from app.core.auth import create_access_token
    from app.database import get_db

    engine, maker, pid = await _mk_db()

    async def _override():
        async with maker() as db:
            yield db

    app.dependency_overrides[get_db] = _override
    old = settings.AUTH_REQUIRED
    settings.AUTH_REQUIRED = True
    try:
        async with maker() as db:
            from sqlalchemy import select
            op = (await db.execute(select(User).where(User.username == "op"))).scalar_one()
            out = (await db.execute(select(User).where(User.username == "out"))).scalar_one()
            t_op, t_out = create_access_token(op), create_access_token(out)
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as cli:
            r = await cli.get("/api/v1/search", params={"project_id": pid, "query": "x"})
            assert r.status_code == 401, f"search open: {r.status_code}"
            r = await cli.get("/api/v1/search", params={"project_id": pid, "query": "x"},
                              headers={"Authorization": f"Bearer {t_out}"})
            assert r.status_code == 403, f"non-member: {r.status_code}"
            h = {"Authorization": f"Bearer {t_op}"}
            r = await cli.get("/api/v1/search",
                              params={"project_id": pid, "query": "x" * 201}, headers=h)
            assert r.status_code == 422, f"long query: {r.status_code}"
            r = await cli.get("/api/v1/search",
                              params={"project_id": pid, "query": "x", "limit": 999999},
                              headers=h)
            assert r.status_code == 422, f"huge limit: {r.status_code}"
            r = await cli.get("/api/v1/search",
                              params={"project_id": pid, "query": "x", "search_in": "logs,evil"},
                              headers=h)
            assert r.status_code == 400, f"bad search_in: {r.status_code}"
            # LIKE metacharacters are literal: % must not match everything.
            r = await cli.get("/api/v1/search",
                              params={"project_id": pid, "query": "%"}, headers=h)
            assert r.status_code == 200 and r.json()["total"] == 0, r.text[:150]
    finally:
        settings.AUTH_REQUIRED = old
        app.dependency_overrides.pop(get_db, None)
    await engine.dispose()
    print("PASS search")


def test_search_validation_and_auth():
    asyncio.run(_run())


if __name__ == "__main__":
    test_search_validation_and_auth()
