"""Concurrent claims converge (409, not 500); stale release tokens rejected."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.core.collaboration import (
    ClaimConflictError, claim_item_scope, release_item_scope)
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.project import Project
from app.models.user import ProjectMember, User


async def _seed(maker):
    async with maker() as db:
        proj = Project(name="P"); db.add(proj); await db.flush()
        grp = ChecklistGroup(project_id=proj.id, name="G"); db.add(grp); await db.flush()
        item = ChecklistItem(group_id=grp.id, name="s0", command_template="true")
        db.add(item); await db.flush()
        u1 = User(username="op1", email="op1@x", password_hash="x", is_active=True)
        u2 = User(username="op2", email="op2@x", password_hash="x", is_active=True)
        db.add_all([u1, u2]); await db.flush()
        db.add_all([ProjectMember(project_id=proj.id, user_id=u1.id),
                    ProjectMember(project_id=proj.id, user_id=u2.id)])
        await db.commit()
        return item.id, u1.id, u2.id


async def _run():
    import tempfile
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    # File DB: the racers need independent connections (shared :memory:
    # connections corrupt session state instead of colliding on the index).
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp.name}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    item_id, u1, u2 = await _seed(maker)

    async def claim_as(user_id):
        async with maker() as db:
            user = await db.get(User, user_id)
            try:
                await claim_item_scope(db, item_id=item_id, host_id=None, user=user)
                await db.commit()
                return "ok"
            except ClaimConflictError:
                return "conflict"

    results = await asyncio.gather(claim_as(u1), claim_as(u2))
    assert sorted(results) == ["conflict", "ok"], f"results={results}"

    # Stale release token: read version, change hands, release with old token.
    async with maker() as db:
        from sqlalchemy import select
        from app.models.checklist_claim import ChecklistClaim
        c = (await db.execute(select(ChecklistClaim).where(
            ChecklistClaim.item_id == item_id))).scalar_one()
        seen_version = c.version
        holder = c.claimed_by_user_id
    other = u2 if holder == u1 else u1
    async with maker() as db:
        user = await db.get(User, other)
        await claim_item_scope(db, item_id=item_id, host_id=None, user=user,
                               force_takeover=True)
        await db.commit()
    async with maker() as db:
        user = await db.get(User, holder)
        try:
            await release_item_scope(db, item_id=item_id, host_id=None,
                                     user=user, expected_version=seen_version)
            await db.commit()
            stale = "released"
        except ClaimConflictError as e:
            stale = getattr(e, "reason", "claimed_by_other")
        assert stale == "version_mismatch", f"stale release must 409, got {stale}"
    await engine.dispose()
    os.unlink(tmp.name)
    print("PASS claim races")


def test_claim_races():
    asyncio.run(_run())


if __name__ == "__main__":
    test_claim_races()
