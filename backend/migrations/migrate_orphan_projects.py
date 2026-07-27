"""Back-fill ProjectMember rows for projects created before collaboration.

Projects that have zero active members become invisible in list_projects
because the query inner-joins on project_members when a user is logged in.

Usage:
    python migrations/migrate_orphan_projects.py                # uses created_by_user_id where available
    python migrations/migrate_orphan_projects.py --user-id 1    # assign ownerless projects to user 1
"""
import argparse
import asyncio
import os
import sys
import uuid
from datetime import datetime

# Add backend directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import engine, async_session_maker, init_db
from app.models.project import Project
from app.models.user import ProjectMember, User


async def migrate(fallback_user_id: int | None = None):
    """Find orphan projects and create ProjectMember rows."""
    await init_db()

    async with async_session_maker() as db:
        # Find projects with no active members
        member_sub = (
            select(ProjectMember.project_id)
            .where(ProjectMember.deleted_at.is_(None))
            .correlate(Project)
        )
        orphans_q = (
            select(Project)
            .where(
                Project.deleted_at.is_(None),
                ~Project.id.in_(member_sub),
            )
        )
        result = await db.execute(orphans_q)
        orphan_projects = result.scalars().all()

        if not orphan_projects:
            print("✓ No orphan projects found — nothing to migrate.")
            return

        print(f"Found {len(orphan_projects)} orphan project(s):\n")

        migrated = 0
        skipped = 0

        for project in orphan_projects:
            owner_id = project.created_by_user_id or fallback_user_id

            if owner_id is None:
                print(f"  ✗ [{project.id}] \"{project.name}\" — no created_by_user_id and no --user-id given, skipping")
                skipped += 1
                continue

            # Verify user exists
            user_exists = await db.execute(
                select(User.id).where(User.id == owner_id, User.is_active.is_(True))
            )
            if user_exists.scalar_one_or_none() is None:
                print(f"  ✗ [{project.id}] \"{project.name}\" — user {owner_id} not found or inactive, skipping")
                skipped += 1
                continue

            member = ProjectMember(
                project_id=project.id,
                user_id=owner_id,
            )
            db.add(member)
            print(f"  ✓ [{project.id}] \"{project.name}\" → assigned to user {owner_id}")
            migrated += 1

        await db.commit()
        print(f"\nDone: {migrated} migrated, {skipped} skipped.")


def main():
    parser = argparse.ArgumentParser(description="Migrate orphan projects by creating ProjectMember rows.")
    parser.add_argument(
        "--user-id",
        type=int,
        default=None,
        help="Fallback user ID to assign projects that have no created_by_user_id",
    )
    args = parser.parse_args()
    asyncio.run(migrate(fallback_user_id=args.user_id))


if __name__ == "__main__":
    main()
