"""Executions: start/cancel/delete/output need auth + membership."""
import asyncio
import os
import sys
from unittest.mock import patch, AsyncMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.main import app
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.execution import Execution, ExecutionStatus
from app.models.project import Project
from app.models.user import ProjectMember, User


async def _mk_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(Project(name="P")); await db.flush()
        from sqlalchemy import select
        p = (await db.execute(select(Project))).scalar_one()
        db.add(ChecklistGroup(project_id=p.id, name="G")); await db.flush()
        g = (await db.execute(select(ChecklistGroup))).scalar_one()
        db.add(ChecklistItem(group_id=g.id, name="s0", command_template="true"))
        db.add(User(username="op", email="op@x", password_hash="x", is_active=True))
        db.add(User(username="out", email="out@x", password_hash="x", is_active=True))
        await db.flush()
        op = (await db.execute(select(User).where(User.username == "op"))).scalar_one()
        item = (await db.execute(select(ChecklistItem))).scalar_one()
        db.add(ProjectMember(project_id=p.id, user_id=op.id))
        db.add(Execution(public_id="ex-1", item_id=item.id, command="true",
                         status=ExecutionStatus.COMPLETED))
        await db.commit()
        ex = (await db.execute(select(Execution))).scalar_one()
        return engine, maker, item.id, ex.id


async def _run():
    from app.config import settings
    from app.core.auth import create_access_token
    from app.database import get_db

    engine, maker, item_id, ex_id = await _mk_db()

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
            r = await cli.get(f"/api/v1/executions/{ex_id}")
            assert r.status_code == 401, f"get open: {r.status_code}"
            r = await cli.post(f"/api/v1/executions/{ex_id}/cancel",
                               headers={"Authorization": f"Bearer {t_out}"})
            assert r.status_code == 403, f"non-member cancel: {r.status_code}"
            r = await cli.get(f"/api/v1/executions/{ex_id}/output",
                              headers={"Authorization": f"Bearer {t_out}"})
            assert r.status_code == 403, f"non-member output: {r.status_code}"
            with patch("app.api.executions.task_orchestrator") as orch:
                orch.create_execution_record = AsyncMock(return_value=None)
                r = await cli.post("/api/v1/executions",
                                   json={"item_id": item_id},
                                   headers={"Authorization": f"Bearer {t_out}"})
                assert r.status_code == 403, f"non-member start: {r.status_code} {r.text[:150]}"
    finally:
        settings.AUTH_REQUIRED = old
        app.dependency_overrides.pop(get_db, None)
    await engine.dispose()
    print("PASS executions auth")


def test_executions_auth_and_scoping():
    asyncio.run(_run())


if __name__ == "__main__":
    test_executions_auth_and_scoping()
