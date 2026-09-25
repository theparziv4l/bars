"""File search query is length-bounded like global search."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from httpx import AsyncClient, ASGITransport
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.main import app
from app.models.project import Project
from app.models.user import ProjectMember, User


async def _run():
    from app.config import settings
    from app.core.auth import create_access_token
    from app.database import get_db

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(Project(name="P"))
        db.add(User(username="op", email="op@x", password_hash="x", is_active=True))
        await db.flush()
        p = (await db.execute(select(Project))).scalar_one()
        op = (await db.execute(select(User).where(User.username == "op"))).scalar_one()
        db.add(ProjectMember(project_id=p.id, user_id=op.id))
        await db.commit()
        pid = p.id

    async def _override():
        async with maker() as db:
            yield db

    app.dependency_overrides[get_db] = _override
    old = settings.AUTH_REQUIRED
    settings.AUTH_REQUIRED = True
    try:
        async with maker() as db:
            op = (await db.execute(select(User).where(User.username == "op"))).scalar_one()
            token = create_access_token(op)
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as cli:
            h = {"Authorization": f"Bearer {token}"}
            r = await cli.get(f"/api/v1/files/search/global?project_id={pid}&query={'x' * 201}",
                              headers=h)
            assert r.status_code == 422, f"overlong query: {r.status_code}"
            r = await cli.get(f"/api/v1/files/search/global?project_id={pid}&query=abc",
                              headers=h)
            assert r.status_code == 200, f"normal query: {r.status_code} {r.text[:120]}"
    finally:
        settings.AUTH_REQUIRED = old
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


def test_search_files_query_bound():
    asyncio.run(_run())


if __name__ == "__main__":
    test_search_files_query_bound()
