"""Bulk parallel fan-out never exceeds the bound."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from unittest.mock import patch


async def _run():
    from app.core.orchestrator import TaskOrchestrator

    orch = TaskOrchestrator()
    live = 0
    peak = 0

    # execute_bulk goes through the shared pre-flight (_bulk_run_one), not execute_item.
    async def _fake_run_one(item_id, host_id, variables, outputs=None):
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.02)
        live -= 1
        return f"exec-{item_id}"

    with patch.object(orch, "_bulk_run_one", _fake_run_one):
        out = await orch.execute_bulk(None, list(range(30)))
    assert len(out) == 30, len(out)
    from app.config import settings
    assert peak <= settings.BULK_CONCURRENCY, f"peak={peak}"
    assert peak > 1, "must still parallelize"


def test_bulk_bound():
    asyncio.run(_run())


if __name__ == "__main__":
    test_bulk_bound()
