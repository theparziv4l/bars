"""Worker loop survives errors; stop() is prompt even mid-backoff."""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.core.sync_worker as worker_mod
from app.core.sync_worker import SyncWorker


async def _loop_survives():
    from unittest.mock import patch
    w = SyncWorker()
    calls = []

    async def flaky():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        w._stopped.set()
        return True

    w.run_once = flaky  # noqa: E731
    with patch.object(worker_mod, "_IDLE_INTERVAL", 0), \
         patch.object(worker_mod.settings, "SYNC_PULL_INTERVAL_SECONDS", 1):
        await w._run_loop()
    assert len(calls) == 2, f"loop must survive first error, calls={calls}"


async def _stop_prompt():
    w = SyncWorker()

    async def sleepy():
        await asyncio.sleep(300)
        return True

    w.run_once = sleepy  # noqa: E731
    w._task = asyncio.create_task(w._run_loop())
    await asyncio.sleep(0.05)
    t0 = time.monotonic()
    await w.stop()
    dt = time.monotonic() - t0
    assert dt < 5, f"stop took {dt:.1f}s"


async def _run():
    await _loop_survives()
    await _stop_prompt()
    print("PASS worker lifecycle")


def test_worker_loop_and_stop():
    asyncio.run(_run())


if __name__ == "__main__":
    test_worker_loop_and_stop()
