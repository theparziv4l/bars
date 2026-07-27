import asyncio
import sys
import os

# Add backend directory to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import engine
from sqlalchemy import text

async def migrate():
    async with engine.begin() as conn:
        print("Checking for host_id column in project_variables...")
        try:
            await conn.execute(text("ALTER TABLE project_variables ADD COLUMN host_id INTEGER REFERENCES hosts(id) ON DELETE CASCADE"))
            print("Successfully added host_id to project_variables")
        except Exception as e:
            if "duplicate column name" in str(e):
                print("Column host_id already exists in project_variables")
            else:
                print(f"Error altering project_variables: {e}")
        
        # Create index if not exists (SQLite doesn't support IF NOT EXISTS for indexes easily in one generic statement, handling via try/except or precise syntax)
        try:
             await conn.execute(text("CREATE INDEX ix_project_variables_host_id ON project_variables (host_id)"))
             print("Created index on host_id")
        except Exception as e:
             if "already exists" in str(e):
                 pass
             else:
                 print(f"Index creation note: {e}")

if __name__ == "__main__":
    asyncio.run(migrate())
