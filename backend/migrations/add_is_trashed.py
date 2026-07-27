import asyncio
import sys
import os

# Add backend directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import engine
from sqlalchemy import text

async def migrate():
    async with engine.begin() as conn:
        print("Checking for is_trashed column in checklist_groups...")
        try:
            await conn.execute(text("ALTER TABLE checklist_groups ADD COLUMN is_trashed BOOLEAN DEFAULT 0"))
            print("Successfully added is_trashed to checklist_groups")
        except Exception as e:
            if "duplicate column name" in str(e):
                print("Column is_trashed already exists in checklist_groups")
            else:
                print(f"Error altering checklist_groups: {e}")

        print("Checking for is_trashed column in checklist_items...")
        try:
            await conn.execute(text("ALTER TABLE checklist_items ADD COLUMN is_trashed BOOLEAN DEFAULT 0"))
            print("Successfully added is_trashed to checklist_items")
        except Exception as e:
            if "duplicate column name" in str(e):
                print("Column is_trashed already exists in checklist_items")
            else:
                print(f"Error altering checklist_items: {e}")

if __name__ == "__main__":
    asyncio.run(migrate())
