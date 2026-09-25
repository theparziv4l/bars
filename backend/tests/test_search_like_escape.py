"""Hosts/projects search treats LIKE metacharacters literally."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.main import app
from app.models.host import Host
from app.models.project import Project


async def _run():
    from app.database import get_db

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(Project(name="P")); await db.flush()
        from sqlalchemy import select
        p = (await db.execute(select(Project))).scalar_one()
        db.add(Host(project_id=p.id, ip_address="10.0.0.1", hostname="a%b"))
        db.add(Host(project_id=p.id, ip_address="10.0.0.2", hostname="axb"))
        db.add(Project(name="100%"))
        db.add(Project(name="1000"))
        await db.commit()

    async def _override():
        async with maker() as db:
            yield db

    app.dependency_overrides[get_db] = _override
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as cli:
            r = await cli.get("/api/v1/hosts", params={"project_id": p.id, "search": "a%b"})
            assert r.status_code == 200, r.text[:150]
            names = [h["hostname"] for h in r.json()["items"]]
            assert names == ["a%b"], names
            r = await cli.get("/api/v1/projects", params={"search": "100%"})
            assert r.status_code == 200, r.text[:150]
            names = [pr["name"] for pr in r.json()["items"]]
            assert names == ["100%"], names
    finally:
        app.dependency_overrides.pop(get_db, None)
    await engine.dispose()
    print("PASS like escape")


def test_like_escape():
    asyncio.run(_run())


if __name__ == "__main__":
    test_like_escape()
