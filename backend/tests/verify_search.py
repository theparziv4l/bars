
import asyncio
from app.database import async_session_maker, init_db
from app.models.host import Host
from app.models.execution import Execution
from app.models.checklist import ChecklistItem, ChecklistGroup
from app.api.search import global_search
from sqlalchemy import select, delete

async def verify_search():
    await init_db()
    async with async_session_maker() as db:
        print("1. Setting up test data...")
        # Create test project (if needed, but we'll assume project_id 1 exists or use existing)
        project_id = 1 
        
        # Create a unique test host
        host = Host(project_id=project_id, ip_address="192.168.99.99", hostname="search-test-host")
        db.add(host)
        
        # Create test group and item
        group = ChecklistGroup(project_id=project_id, name="Test Group")
        db.add(group)
        await db.flush()
        
        item = ChecklistItem(group_id=group.id, name="Test Item", command_template="echo test")
        db.add(item)
        await db.flush()
        
        # Create test executions with specific unique strings
        # Old execution (should appear last)
        exec_old = Execution(
            item_id=item.id, host_id=host.id, command="echo old",
            stdout="This is an old log entry with unique_key_xyz",
            status="completed"
        )
        # New execution (should appear first)
        exec_new = Execution(
            item_id=item.id, host_id=host.id, command="echo new",
            stdout="This is a NEW log entry with unique_key_xyz",
            status="completed"
        )
        db.add(exec_old)
        db.add(exec_new)
        await db.commit()
        
        print("2. Testing Search...")
        # Search for the unique key
        results = await global_search(
            project_id=project_id,
            query="unique_key_xyz",
            db=db,
            limit=10,
            search_in="logs"
        )
        
        logs = results["logs"]
        print(f"Found {len(logs)} log results.")
        
        if len(logs) >= 2:
            first_match = logs[0]["matches"][0]["line"]
            second_match = logs[1]["matches"][0]["line"]
            
            print(f"First result: {first_match}")
            print(f"Second result: {second_match}")
            
            if "NEW" in first_match and "old" in first_match.lower():
                 print("FAIL: Sorting order incorrect.")
            elif "NEW" in first_match:
                print("SUCCESS: Newest log appeared first!")
            else:
                print("FAIL: Newest log did not appear first.")
        else:
            print("FAIL: Did not find both inserted logs.")

        # Cleanup
        print("3. Cleaning up...")
        await db.delete(exec_new)
        await db.delete(exec_old)
        await db.delete(item)
        await db.delete(group)
        await db.delete(host)
        await db.commit()

if __name__ == "__main__":
    asyncio.run(verify_search())
