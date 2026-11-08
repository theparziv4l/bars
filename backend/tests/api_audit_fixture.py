"""Shared API test env: in-memory DB, users op (member of every project the
test adds via `member_of`) and out (no membership), auth enforced."""
import os
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.main import app
from app.models.user import ProjectMember, User


@asynccontextmanager
async def api_env(db_url="sqlite+aiosqlite:///:memory:"):
    from app.config import settings
    from app.core.auth import create_access_token
    from app.database import get_db

    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        op = User(username="op", email="op@x", password_hash="x", is_active=True)
        out = User(username="out", email="out@x", password_hash="x", is_active=True)
        db.add_all([op, out])
        await db.commit()
        h_op = {"Authorization": f"Bearer {create_access_token(op)}"}
        h_out = {"Authorization": f"Bearer {create_access_token(out)}"}
        op_id, out_id = op.id, out.id

    async def member_of(*project_ids):
        async with maker() as db:
            for pid in project_ids:
                db.add(ProjectMember(project_id=pid, user_id=op_id))
            await db.commit()

    async def _override():
        async with maker() as db:  # mirrors app.database.get_db
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    app.dependency_overrides[get_db] = _override
    old = settings.AUTH_REQUIRED
    settings.AUTH_REQUIRED = True
    try:
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as cli:
            yield SimpleNamespace(maker=maker, cli=cli, op=h_op, out=h_out,
                                  op_id=op_id, out_id=out_id, member_of=member_of)
    finally:
        settings.AUTH_REQUIRED = old
        app.dependency_overrides.pop(get_db, None)
        await engine.dispose()
