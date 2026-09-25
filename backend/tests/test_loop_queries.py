"""Loop-invariant queries run once per request, not once per row."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401


async def _count_statements(engine, coro_factory, needle):
    hits = []

    def _before(conn, cursor, statement, parameters, context, executemany):
        if needle in str(statement):
            hits.append(str(statement))

    event.listen(engine.sync_engine, "before_cursor_execute", _before)
    try:
        await coro_factory()
    finally:
        event.remove(engine.sync_engine, "before_cursor_execute", _before)
    return len(hits)


async def _run_import():
    from app.models.ad_domain import ADDomain
    from app.models.checklist import ChecklistGroup, ChecklistItem
    from app.models.project import Project
    from app.models.user import ProjectMember, User

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(Project(name="P")); await db.flush()
        from sqlalchemy import select
        p = (await db.execute(select(Project))).scalar_one()
        db.add(ADDomain(project_id=p.id, name="corp"))
        db.add(User(username="op", email="o@x", password_hash="x", is_active=True,
                    role="admin"))
        await db.flush()
        op = (await db.execute(select(User))).scalar_one()
        db.add(ProjectMember(project_id=p.id, user_id=op.id))
        for i in range(3):
            g = ChecklistGroup(project_id=None, name=f"T{i}", is_template=True)
            db.add(g); await db.flush()
            db.add(ChecklistItem(group_id=g.id, name="s", command_template="true"))
        await db.commit()
        ids = [g.id for g in (await db.execute(select(ChecklistGroup))).scalars().all()]
        dom = (await db.execute(select(ADDomain))).scalar_one()

    from app.api.checklists import import_checklist
    async with maker() as db:
        from sqlalchemy import select
        op = (await db.execute(select(User))).scalar_one()

        async def _call():
            await import_checklist(p.id, ids, db, op, ad_domain_id=dom.id)

        n = await _count_statements(engine, _call, "ad_domains")
    assert n == 1, f"domain queried {n}x for one import"
    await engine.dispose()


async def _run_migration():
    from app.api.projects import migrate_orphan_projects
    from app.models.project import Project
    from app.models.user import User

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        db.add(User(username="op", email="o@x", password_hash="x", is_active=True,
                    role="admin"))
        await db.flush()
        from sqlalchemy import select
        op = (await db.execute(select(User))).scalar_one()
        for i in range(3):
            db.add(Project(name=f"O{i}", created_by_user_id=op.id))
        await db.commit()

    async with maker() as db:
        from sqlalchemy import select
        op = (await db.execute(select(User))).scalar_one()

        async def _call():
            await migrate_orphan_projects(db, op)

        n = await _count_statements(engine, _call, "users")
    assert n <= 2, f"user lookups: {n} for 3 projects"
    await engine.dispose()


def test_import_domain_queried_once():
    asyncio.run(_run_import())


def test_migration_users_batched():
    asyncio.run(_run_migration())


if __name__ == "__main__":
    test_import_domain_queried_once()
    test_migration_users_batched()
