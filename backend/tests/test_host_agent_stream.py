"""host_agent /run_and_stream robustness: long lines, background children, bad cwd,
stdin, client disconnect."""
import asyncio
import importlib.util
import os
import time

import httpx

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["HOST_AGENT_TOKEN"] = "testtoken"


def _load_agent():
    spec = importlib.util.spec_from_file_location(
        "host_agent_main_under_test", os.path.join(_ROOT, "host_agent", "main.py")
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.AGENT_TOKEN = "testtoken"
    return mod


agent = _load_agent()
HEADERS = {"X-Bars-Token": "testtoken"}


async def _stream(body: dict, total: float = 10.0) -> str:
    transport = httpx.ASGITransport(app=agent.app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        async def go():
            r = await c.post("/run_and_stream", json=body, headers=HEADERS)
            return r.text
        return await asyncio.wait_for(go(), timeout=total)


def test_long_line_does_not_hang():
    out = asyncio.run(_stream({"command": "python3 -c \"print('x'*200000)\""}))
    assert "event: exit\ndata: 0" in out
    assert "x" * 200000 in out


def test_background_child_holding_pipes_does_not_hang():
    t0 = time.monotonic()
    out = asyncio.run(_stream({"command": "sleep 30 & echo hi", "timeout": 2}, total=15))
    assert "stdout:hi" in out
    assert "event: exit" in out
    assert time.monotonic() - t0 < 14


def test_missing_cwd_yields_error_and_exit():
    out = asyncio.run(_stream({"command": "echo hi", "cwd": "/nonexistent/dir/xyz"}))
    assert "data: stderr:" in out
    assert "event: exit\ndata: -1" in out


def test_stdin_delivered_and_devnull_default():
    out = asyncio.run(_stream({"command": "cat", "stdin": "a\nb\n"}))
    assert "stdout:a\n" in out and "stdout:b\n" in out and "data: 0" in out
    # No stdin supplied: cat must see EOF immediately, not wait on the agent's TTY.
    out = asyncio.run(_stream({"command": "cat; echo done"}, total=5))
    assert "stdout:done" in out


def test_large_stdin_no_deadlock():
    out = asyncio.run(_stream({"command": "cat", "stdin": ("y" * 99 + "\n") * 5000}, total=10))
    assert out.count("stdout:") == 5000


def test_client_disconnect_kills_process():
    async def run():
        resp = await agent.run_and_stream(agent.ExecuteRequest(command="sleep 60"))
        gen = resp.body_iterator
        task = asyncio.ensure_future(gen.__anext__())
        await asyncio.sleep(0.5)
        proc = next(iter(agent.active_tasks.values()))
        task.cancel()
        try:
            await task
        except BaseException:
            pass
        await gen.aclose()
        await asyncio.wait_for(proc.wait(), timeout=5)
        assert proc.returncode is not None
        assert not agent.active_tasks

    asyncio.run(run())
