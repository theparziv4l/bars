"""Streaming output is accumulated in chunks, not re-copied per line (item 5)."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import orch_lc  # noqa: F401  (sys.path)
from orch_fixture import stub_stream

from app.core.orchestrator import TaskContext, TaskOrchestrator


class _Exec:
    """Execution stand-in counting writes to stdout."""

    def __init__(self):
        self.id = 1
        self.command = "x"
        self.exit_code = None
        self.stderr = None
        self.alerts_triggered = None
        self.stdout_writes = 0
        self._stdout = None

    @property
    def stdout(self):
        return self._stdout

    @stdout.setter
    def stdout(self, v):
        self.stdout_writes += 1
        self._stdout = v


def test_stdout_not_reassigned_per_line():
    n = 5000
    events = [{"stream": "stdout", "text": f"line{i}"} for i in range(n)]
    events += [{"stream": "stderr", "text": "warn"}, {"exit_code": 0}]
    orch = TaskOrchestrator()
    ex, ctx = _Exec(), TaskContext(execution_id=1)
    item = SimpleNamespace(alert_patterns={})

    with patch("app.core.host_runner.host_runner.execute_and_stream", stub_stream(events)), \
         patch("app.core.orchestrator.notification_manager") as nm:
        nm.notify = AsyncMock(return_value=None)
        asyncio.run(orch._run_subprocess(ex, ctx, item, None, 5))

    assert ex.stdout_writes <= 2, f"stdout re-assigned {ex.stdout_writes}x (O(n^2) copy)"
    assert ex.stdout == "".join(f"line{i}\n" for i in range(n))
    assert ex.stderr == "warn\n"
    # readers of the live buffers still see the joined text
    assert ctx.stdout_buffer == ex.stdout
    assert ctx.stderr_buffer == "warn\n"
