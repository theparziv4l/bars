"""Hosts + AD domains: auth enforced, member-scoped, import fixed."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.main import app
from app.models.ad_domain import ADDomain
from app.models.host import Host
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
        db.add(Host(project_id=p.id, ip_address="10.0.0.1"))
        db.add(ADDomain(project_id=p.id, name="corp.local"))
        db.add(User(username="op", email="op@x", password_hash="x", is_active=True))
        db.add(User(username="out", email="out@x", password_hash="x", is_active=True))
        await db.flush()
        op = (await db.execute(select(User).where(User.username == "op"))).scalar_one()
        h = (await db.execute(select(Host))).scalar_one()
        db.add(ProjectMember(project_id=p.id, user_id=op.id))
        await db.commit()
        return engine, maker, p.id, h.id


async def _run():
    from app.config import settings
    from app.core.auth import create_access_token
    from app.database import get_db

    engine, maker, pid, hid = await _mk_db()

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
        # The ad_domains fallback must be gone: real dep from core.auth.
        import app.api.ad_domains as ad
        import app.core.auth as core_auth
        assert ad.get_current_user_optional is core_auth.get_current_user_optional
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as cli:
            r = await cli.get("/api/v1/hosts", params={"project_id": pid})
            assert r.status_code == 401, f"hosts open: {r.status_code}"
            r = await cli.get(f"/api/v1/hosts/{hid}",
                              headers={"Authorization": f"Bearer {t_out}"})
            assert r.status_code == 403, f"non-member host: {r.status_code}"
            r = await cli.get("/api/v1/ad-domains", params={"project_id": pid},
                              headers={"Authorization": f"Bearer {t_out}"})
            assert r.status_code == 403, f"non-member domains: {r.status_code} {r.text[:120]}"
    finally:
        settings.AUTH_REQUIRED = old
        app.dependency_overrides.pop(get_db, None)
    await engine.dispose()
    print("PASS hosts/ad auth")


def test_hosts_ad_auth_and_scoping():
    asyncio.run(_run())


if __name__ == "__main__":
    test_hosts_ad_auth_and_scoping()
