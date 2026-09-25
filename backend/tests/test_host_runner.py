"""Agent cancel is bounded; transport failures are marked, not fake exits."""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.host_runner import HostRunner


class _HangingResp:
    status = 200

    async def __aenter__(self):
        await asyncio.sleep(300)
        return self

    async def __aexit__(self, *a):
        return False


class _HangingSession:
    def post(self, *a, **k):
        return _HangingResp()


async def _run():
    r = HostRunner(agent_url="http://127.0.0.1:1")

    # 1) cancel bounded.
    async def hanging_session():
        return _HangingSession()

    r._get_session = hanging_session
    t0 = time.monotonic()
    assert await r.cancel_execution("42", timeout=0.1) is False
    assert time.monotonic() - t0 < 5, "cancel must time out fast"

    # 2) transport failure marker (connection refused on loopback).
    r2 = HostRunner(agent_url="http://127.0.0.1:1")
    events = [e async for e in r2.execute_and_stream("echo hi")]
    assert any("transport_error" in e for e in events), f"events={events}"
    assert not any("exit_code" in e for e in events), f"no fake exit: {events}"
    print("PASS host runner")


def test_host_runner_bounds_and_marks():
    asyncio.run(_run())


if __name__ == "__main__":
    test_host_runner_bounds_and_marks()
