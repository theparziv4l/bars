"""Batch aggregation matches int-stored parent ids, flattens, dedupes; merge strips internals."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.core.flow_manager import flow_manager
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.execution import Execution, ExecutionStatus
from app.models.project import Project


async def _agg():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        proj = Project(name="P"); db.add(proj); await db.flush()
        grp = ChecklistGroup(project_id=proj.id, name="G"); db.add(grp); await db.flush()
        item = ChecklistItem(group_id=grp.id, name="I", command_template="true")
        db.add(item); await db.flush()
        parent = Execution(item_id=item.id, command="b", status=ExecutionStatus.COMPLETED,
                           variables_used={"is_batch_parent": True})
        db.add(parent); await db.flush()

        def child(pid, out):
            return Execution(item_id=item.id, command="c", status=ExecutionStatus.COMPLETED,
                             variables_used={"parent_execution_id": pid, "target": "x"},
                             parsed_output=out)
        db.add_all([
            child(parent.id, {"ports": {"value": ["80", "443"]}, "os": {"value": "linux"}}),
            child(str(parent.id), {"ports": {"value": ["443", "22"]}}),  # legacy string id
            child(parent.id + 99, {"ports": {"value": ["9999"]}}),       # other parent
        ])
        await db.commit()
        agg = await flow_manager._aggregate_batch_outputs(db, parent.id)
    await engine.dispose()
    return agg


def test_batch_aggregate_matches_int_flattens_dedupes():
    agg = asyncio.run(_agg())
    assert agg["ports"] == ["80", "443", "22"], agg
    assert agg["os"] == ["linux"], agg


def test_merge_strips_internal_keys_and_skips_children():
    base = {}
    vars_parent = {"target": "10.0.0.0/24", "is_batch_parent": True, "target_count": 3,
                   "parent_execution_id": 1, "keep": "v"}
    # A batch parent has no parent_execution_id in reality; here it is stripped regardless.
    vars_parent.pop("parent_execution_id")
    flow_manager._merge_stage_vars(base, vars_parent)
    assert base == {"target": "10.0.0.0/24", "keep": "v"}, base
    base2 = {}
    assert flow_manager._merge_stage_vars(base2, {"target": "1.1.1.1", "parent_execution_id": 5}) is False
    assert base2 == {}
