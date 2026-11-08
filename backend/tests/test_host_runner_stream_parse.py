"""host_runner: leading whitespace preserved, execute_sync joins with newline,
stdin forwarded, per-request total timeout, no unbounded session timeout."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.host_runner import HostRunner


class _Content:
    def __init__(self, data: bytes):
        self._data = data

    async def read(self, n):
        d, self._data = self._data[:n], self._data[n:]
        return d


class _Resp:
    status = 200

    def __init__(self, data: bytes):
        self.content = _Content(data)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Session:
    def __init__(self, data: bytes):
        self.data = data
        self.calls = []

    def post(self, url, json=None, **kw):
        self.calls.append((json, kw))
        return _Resp(self.data)


def _runner(data: bytes):
    r = HostRunner(agent_url="http://x")
    s = _Session(data)

    async def gs():
        return s

    r._get_session = gs
    return r, s


SSE = (
    b"data: stdout:10.0.0.1\n\n"
    b"data: stdout:  indented col\r\n\n"
    b"data: stderr:  warn\n\n"
    b"data: stdout:10.0.0.2\n\n"
    b"event: exit\ndata: 0\n\n"
)


def test_leading_whitespace_preserved_and_sync_joins_newline():
    async def run():
        r, s = _runner(SSE)
        res = await r.execute_sync("cmd", cwd="/tmp", stdin="in\n")
        assert res["stdout"] == "10.0.0.1\n  indented col\n10.0.0.2"
        assert res["stderr"] == "  warn"
        assert res["exit_code"] == 0
        body, kw = s.calls[0]
        assert body["stdin"] == "in\n"
        assert kw["timeout"].total > 60

    asyncio.run(run())


def test_stdin_omitted_when_none():
    async def run():
        r, s = _runner(SSE)
        await r.execute_sync("cmd")
        assert "stdin" not in s.calls[0][0]

    asyncio.run(run())
