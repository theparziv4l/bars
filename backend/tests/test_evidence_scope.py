"""Evidence list: finding_id filter resolves the finding's project for membership."""
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
from app.models.evidence import Evidence
from app.models.finding import Finding
from app.models.project import Project
from app.models.user import ProjectMember, User


async def _mk_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(Project(name="P"))
        await db.flush()
        p = (await db.execute(select(Project))).scalar_one()
        db.add(Finding(project_id=p.id, title="F"))
        await db.flush()
        f = (await db.execute(select(Finding))).scalar_one()
        db.add(Evidence(project_id=p.id, finding_id=f.id, kind="text",
                        caption="c", content="s3cret"))
        db.add(User(username="op", email="op@x", password_hash="x", is_active=True))
        db.add(User(username="out", email="out@x", password_hash="x", is_active=True))
        await db.flush()
        op = (await db.execute(select(User).where(User.username == "op"))).scalar_one()
        db.add(ProjectMember(project_id=p.id, user_id=op.id))
        await db.commit()
        return engine, maker, f.id


async def _run():
    from app.config import settings
    from app.core.auth import create_access_token
    from app.database import get_db

    engine, maker, fid = await _mk_db()

    async def _override():
        async with maker() as db:
            yield db

    app.dependency_overrides[get_db] = _override
    old = settings.AUTH_REQUIRED
    settings.AUTH_REQUIRED = True
    try:
        async with maker() as db:
            op = (await db.execute(select(User).where(User.username == "op"))).scalar_one()
            out = (await db.execute(select(User).where(User.username == "out"))).scalar_one()
            t_op, t_out = create_access_token(op), create_access_token(out)
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as cli:
            r = await cli.get(
                f"/api/v1/evidence?finding_id={fid}",
                headers={"Authorization": f"Bearer {t_out}"})
            assert r.status_code == 403, f"outsider finding filter: {r.status_code}"
            r = await cli.get(
                f"/api/v1/evidence?finding_id={fid}",
                headers={"Authorization": f"Bearer {t_op}"})
            assert r.status_code == 200, f"member finding filter: {r.status_code}"
            # Unfiltered auth-only reads stay per plan (by-design, pinned here).
            r = await cli.get("/api/v1/evidence",
                              headers={"Authorization": f"Bearer {t_out}"})
            assert r.status_code == 200, f"outsider unfiltered: {r.status_code}"
    finally:
        settings.AUTH_REQUIRED = old
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()


def test_evidence_finding_scope():
    asyncio.run(_run())


if __name__ == "__main__":
    test_evidence_finding_scope()
