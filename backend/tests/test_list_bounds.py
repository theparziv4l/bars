"""Findings list and host timeline respect limits."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.main import app
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.execution import Execution, ExecutionStatus
from app.models.finding import Finding
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
        db.add(Host(project_id=p.id, ip_address="10.0.0.1")); await db.flush()
        h = (await db.execute(select(Host))).scalar_one()
        db.add(ChecklistGroup(project_id=p.id, name="G")); await db.flush()
        g = (await db.execute(select(ChecklistGroup))).scalar_one()
        db.add(ChecklistItem(group_id=g.id, name="s0", command_template="true"))
        await db.flush()
        item = (await db.execute(select(ChecklistItem))).scalar_one()
        for i in range(5):
            db.add(Finding(project_id=p.id, title=f"F{i}"))
            db.add(Execution(public_id=f"ex-{i}", item_id=item.id, host_id=h.id,
                             command="true", status=ExecutionStatus.COMPLETED,
                             stdout="x" * 10000))
        await db.commit()

    async def _override():
        async with maker() as db:
            yield db

    app.dependency_overrides[get_db] = _override
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as cli:
            r = await cli.get("/api/v1/findings", params={"project_id": p.id, "limit": 2})
            assert r.status_code == 200, r.text[:150]
            assert len(r.json()) == 2, len(r.json())
            r = await cli.get(f"/api/v1/hosts/{h.id}/timeline", params={"limit": 3})
            assert r.status_code == 200, r.text[:150]
            rows = r.json()
            assert len(rows) == 3, len(rows)
            assert rows[0]["id"] > rows[-1]["id"], "newest-first"
            assert "stdout" not in rows[0]
    finally:
        app.dependency_overrides.pop(get_db, None)
    await engine.dispose()
    print("PASS list bounds")


def test_list_bounds():
    asyncio.run(_run())


if __name__ == "__main__":
    test_list_bounds()
