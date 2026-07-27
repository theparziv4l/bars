import asyncio
import sys
import os
from datetime import datetime

# Add backend to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.database import async_session_maker, init_db
from app.models.project import Project
from app.models.host import Host
from app.models.variable import ProjectVariable
from app.core.orchestrator import task_orchestrator
from app.models.checklist import ChecklistItem, ChecklistGroup

async def test_host_variables():
    print("0. Initializing Test Database...")
    await init_db()
    
    async with async_session_maker() as db:
        print("1. Setting up test data...")
        # Create Project
        proj = Project(name=f"Test Project {datetime.now().timestamp()}")
        db.add(proj)
        await db.flush()
        
        # Create Host A and Host B
        host_a = Host(project_id=proj.id, ip_address="192.168.1.10")
        host_b = Host(project_id=proj.id, ip_address="192.168.1.20")
        db.add_all([host_a, host_b])
        await db.flush()
        
        print(f"Project ID: {proj.id}, Host A: {host_a.id}, Host B: {host_b.id}")
        
        # 2. Simulate Output Parsing & Injection for Host A
        # Mock item
        group = ChecklistGroup(project_id=proj.id, name="Test Group")
        db.add(group)
        await db.flush()
        
        item = ChecklistItem(group_id=group.id, name="Test Item", command_template="echo test")
        db.add(item)
        await db.flush()
        
        # Mock Execution for Host A
        from app.models.execution import Execution, ExecutionOutput, ExecutionStatus
        exec_a = Execution(
            item_id=item.id,
            host_id=host_a.id,
            status=ExecutionStatus.COMPLETED,
            command="test_cmd"
        )
        db.add(exec_a)
        await db.flush()
        
        # Parse result (simulated)
        # We manually call _process_variable_injection
        from collections import namedtuple
        ParsedResult = namedtuple("ParsedResult", ["value", "data_type"])
        
        parsed_results = {
            "open_ports": ParsedResult(value="80,443", data_type="string")
        }
        
        print("2. Injecting variable 'open_ports'='80,443' for Host A...")
        await task_orchestrator._process_variable_injection(db, exec_a, item, parsed_results)
        
        # Verify Variable Created
        stmt = f"SELECT * FROM project_variables WHERE project_id={proj.id}"
        # using ORM
        from sqlalchemy import select
        vars = (await db.execute(select(ProjectVariable).where(ProjectVariable.project_id == proj.id))).scalars().all()
        for v in vars:
            print(f"Variable: key={v.key}, value={v.value}, host_id={v.host_id}")
            
        assert any(v.key == "open_ports" and v.host_id == host_a.id and v.value == "80,443" for v in vars)
        
        # 3. Simulate Output Parsing for Host B (Different value)
        exec_b = Execution(
            item_id=item.id,
            host_id=host_b.id,
            status=ExecutionStatus.COMPLETED,
            command="test_cmd_b"
        )
        db.add(exec_b)
        await db.flush()
        
        parsed_results_b = {
            "open_ports": ParsedResult(value="22,8080", data_type="string")
        }
        
        print("3. Injecting variable 'open_ports'='22,8080' for Host B...")
        await task_orchestrator._process_variable_injection(db, exec_b, item, parsed_results_b)
        
        vars = (await db.execute(select(ProjectVariable).where(ProjectVariable.project_id == proj.id))).scalars().all()
        for v in vars:
            print(f"Variable: key={v.key}, value={v.value}, host_id={v.host_id}")
            
        assert any(v.key == "open_ports" and v.host_id == host_b.id and v.value == "22,8080" for v in vars)
        
        # 4. Test Retrieval for Host A
        print("4. Testing Retrieval for Host A...")
        # Prepare variables for a new execution on Host A
        # We simulate what execute_item does: calls _prepare_command_arguments logic which relies on Loaded Variables
        # But retrieval logic is inside execute_item.
        
        # Let's reproduce the retrieval logic block
        # (Copy-paste of logic we added to orchestrator)
        variables = {} 
        host_id = host_a.id
        project_id = proj.id
        
        # GLOBAL
        global_vars = await db.execute(select(ProjectVariable).where(
            ProjectVariable.project_id == project_id,
            ProjectVariable.host_id.is_(None)
        ))
        
        # HOST
        host_vars = await db.execute(select(ProjectVariable).where(
            ProjectVariable.project_id == project_id,
            ProjectVariable.host_id == host_id
        ))
        
        merged = {}
        for v in global_vars.scalars(): merged[v.key] = v.value
        for v in host_vars.scalars(): merged[v.key] = v.value
        
        print(f"Resolved Variables for Host A: {merged}")
        assert merged.get("open_ports") == "80,443"
        
        # 5. Test Retrieval for Host B
        print("5. Testing Retrieval for Host B...")
        host_id = host_b.id
        host_vars_b = await db.execute(select(ProjectVariable).where(
            ProjectVariable.project_id == project_id,
            ProjectVariable.host_id == host_id
        ))
        
        merged_b = {}
        # Re-fetch global (conceptually)
        for v in host_vars_b.scalars(): merged_b[v.key] = v.value
        
        print(f"Resolved Variables for Host B: {merged_b}")
        assert merged_b.get("open_ports") == "22,8080"
        
        print("\nSUCCESS: Host-specific variables verified.")
        
        # Cleanup
        # await db.delete(proj)
        # await db.commit()

if __name__ == "__main__":
    asyncio.run(test_host_variables())
