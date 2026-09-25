"""Batch aggregates expose inner values, not wrapper dicts."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.database import Base
import app.models  # noqa: F401
from app.models.execution import Execution


async def _run():
    from app.core.flow_manager import _unwrap_output_value
    assert _unwrap_output_value({"value": 80, "data_type": "int"}) == 80
    assert _unwrap_output_value("plain") == "plain"
    assert _unwrap_output_value({"data_type": "int"}) is None  # mirrors orch .get
    print("PASS batch agg")


def test_batch_agg_unwraps():
    asyncio.run(_run())


if __name__ == "__main__":
    test_batch_agg_unwraps()
