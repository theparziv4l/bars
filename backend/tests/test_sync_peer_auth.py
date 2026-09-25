"""Sync pull/push require the peer token; worker sends it."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.main import app


async def _run():
    from app.config import settings
    from app.database import get_db

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    async def _override():
        async with maker() as db:
            yield db

    app.dependency_overrides[get_db] = _override
    old_en, old_tok = settings.SYNC_ENABLED, getattr(settings, "SYNC_PEER_TOKEN", "")
    settings.SYNC_ENABLED = True
    settings.SYNC_PEER_TOKEN = "s3cret"
    try:
        from app.core.sync_worker import sync_worker
        assert sync_worker._peer_headers() == {"X-Sync-Token": "s3cret"}
        async with AsyncClient(transport=ASGITransport(app=app),
                               base_url="http://t") as cli:
            r = await cli.get("/api/v1/sync/pull")
            assert r.status_code == 401, f"pull open: {r.status_code}"
            r = await cli.get("/api/v1/sync/pull", headers={"X-Sync-Token": "wrong"})
            assert r.status_code == 401, f"pull bad token: {r.status_code}"
            r = await cli.get("/api/v1/sync/pull", headers={"X-Sync-Token": "s3cret"})
            assert r.status_code == 200, f"pull good token: {r.status_code} {r.text[:150]}"
            r = await cli.post("/api/v1/sync/push",
                               json={"source_node_id": "n1", "events": []})
            assert r.status_code == 401, f"push open: {r.status_code}"
            # Fail closed: sync on, no token configured -> 503, never open.
            settings.SYNC_PEER_TOKEN = ""
            r = await cli.get("/api/v1/sync/pull", headers={"X-Sync-Token": "s3cret"})
            assert r.status_code == 503, f"unconfigured: {r.status_code}"
    finally:
        settings.SYNC_ENABLED = old_en
        settings.SYNC_PEER_TOKEN = old_tok
        app.dependency_overrides.pop(get_db, None)
    await engine.dispose()
    print("PASS sync peer auth")


def test_sync_peer_auth():
    asyncio.run(_run())


if __name__ == "__main__":
    test_sync_peer_auth()
