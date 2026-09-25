"""Evidence: upload capped, serve contained + scoped, auth enforced."""
import asyncio
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.main import app
from app.models.evidence import Evidence
from app.models.project import Project
from app.models.user import ProjectMember, User

MAX_BYTES = 25 * 1024 * 1024


async def _mk_db(storage):
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
        # Evidence row pointing outside STORAGE_PATH (must never serve).
        db.add(Evidence(project_id=p.id, kind="file", filename="x",
                        local_path="/etc/hostname"))
        await db.commit()
        return engine, maker, p.id


async def _run():
    import tempfile
    from pathlib import Path
    from app.config import settings
    from app.core.auth import create_access_token
    from app.database import get_db

    tmp = Path(tempfile.mkdtemp())
    engine, maker, pid = await _mk_db(tmp)

    async def _override():
        async with maker() as db:
            yield db

    app.dependency_overrides[get_db] = _override
    old_auth, old_storage = settings.AUTH_REQUIRED, settings.STORAGE_PATH
    settings.AUTH_REQUIRED = True
    settings.STORAGE_PATH = tmp
    try:
        async with maker() as db:
            from sqlalchemy import select
            op = (await db.execute(select(User).where(User.username == "op"))).scalar_one()
            out = (await db.execute(select(User).where(User.username == "out"))).scalar_one()
            ev = (await db.execute(select(Evidence))).scalar_one()
            t_op, t_out = create_access_token(op), create_access_token(out)
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as cli:
            r = await cli.get("/api/v1/evidence", params={"project_id": pid})
            assert r.status_code == 401, f"list open: {r.status_code}"
            # Oversize upload rejected without buffering the whole body twice.
            big = io.BytesIO(b"x" * (MAX_BYTES + 1))
            r = await cli.post("/api/v1/evidence",
                               files={"file": ("big.bin", big)},
                               data={"project_id": str(pid)},
                               headers={"Authorization": f"Bearer {t_op}"})
            assert r.status_code == 413, f"oversize: {r.status_code}"
            # Escaped local_path never serves.
            r = await cli.get(f"/api/v1/evidence/{ev.id}/serve",
                              headers={"Authorization": f"Bearer {t_op}"})
            assert r.status_code == 404, f"escape serve: {r.status_code}"
            # Non-member cannot list project evidence.
            r = await cli.get("/api/v1/evidence", params={"project_id": pid},
                              headers={"Authorization": f"Bearer {t_out}"})
            assert r.status_code == 403, f"non-member list: {r.status_code}"
    finally:
        settings.AUTH_REQUIRED = old_auth
        settings.STORAGE_PATH = old_storage
        app.dependency_overrides.pop(get_db, None)
    await engine.dispose()
    print("PASS evidence")


def test_evidence_caps_scoping_auth():
    asyncio.run(_run())


if __name__ == "__main__":
    test_evidence_caps_scoping_auth()
