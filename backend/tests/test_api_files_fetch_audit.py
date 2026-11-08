"""Audit 39: fetch/upload build shell commands from target-controlled names.

Commands are executed for real in /bin/sh with a stub `smbclient` that records
its argv, so shell injection shows up as a marker file / wrong argv.
"""
import asyncio
import os
import subprocess
import tempfile

from tests.api_audit_fixture import api_env
from app.core.host_runner import host_runner
from app.models.file import DiscoveredFile
from app.models.host import Host
from app.models.project import Project


class _Shell:
    def __init__(self):
        self.stub = tempfile.mkdtemp()
        self.log = os.path.join(self.stub, "argv.log")
        self.marker = os.path.join(self.stub, "PWNED")
        self.cmds = []
        sm = os.path.join(self.stub, "smbclient")
        with open(sm, "w") as f:
            f.write('#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a" >> ' + self.log
                    + '; done\necho ---- >> ' + self.log + '\n')
        os.chmod(sm, 0o755)

    async def execute_sync(self, cmd, *a, **k):
        self.cmds.append(cmd)
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True,
                           cwd=self.stub,
                           env={**os.environ, "PATH": self.stub + ":" + os.environ["PATH"]})
        return {"exit_code": p.returncode, "stdout": p.stdout, "stderr": p.stderr}

    def argv(self):
        if not os.path.exists(self.log):
            return []
        return [c.splitlines() for c in open(self.log).read().split("----\n") if c]

    @property
    def pwned(self):
        return os.path.exists(self.marker)


async def _seed(env, tmp, share, path, name):
    async with env.maker() as db:
        p = Project(name="A", extra_data={
            "project_path": tmp,
            "smb_credentials": {"username": "u", "password": "p", "domain": ""}})
        db.add(p); await db.flush()
        h = Host(project_id=p.id, ip_address="10.0.0.1"); db.add(h); await db.flush()
        f = DiscoveredFile(host_id=h.id, share_name=share, path=path, name=name)
        db.add(f); await db.commit()
        pid, fid, hid = p.id, f.id, h.id
    await env.member_of(pid)
    return fid, hid


def _fetch(share="SHARE", path="docs", name="a.txt"):
    sh = _Shell()
    out = {}
    share, path, name = (x.replace("{M}", "touch PWNED") for x in (share, path, name))

    async def go():
        orig = host_runner.execute_sync
        host_runner.execute_sync = sh.execute_sync
        try:
            with tempfile.TemporaryDirectory() as tmp:
                async with api_env() as env:
                    fid, _ = await _seed(env, tmp, share, path, name)
                    out["r"] = await env.cli.post(f"/api/v1/files/{fid}/fetch",
                                                  headers=env.op, json={})
                    out["tmp"] = tmp
        finally:
            host_runner.execute_sync = orig
    asyncio.run(go())
    return out["r"], sh, out["tmp"]


def test_fetch_shell_metachars_are_quoted():
    r, sh, tmp = _fetch(share="$({M})", path="`{M}`/x y", name="a.txt")
    assert r.status_code == 200, r.text
    assert not sh.pwned
    (argv,) = sh.argv()
    assert argv[0] == "//10.0.0.1/$(touch PWNED)"
    assert argv[-2] == "-c" and argv[-1].startswith('get "')


def test_fetch_double_quote_or_semicolon_names_rejected():
    for name in ('a"; !id.txt', "a;b.txt", "a\nb.txt"):
        r, sh, _ = _fetch(name=name)
        assert r.status_code == 400, (name, r.status_code, r.text)
        assert sh.cmds == []


def test_fetch_rejects_traversal():
    for share, path, name in (("S", "../../../../etc", "passwd"),
                              ("S", "docs", "../../x"),
                              ("..", "docs", "x"),
                              ("S", "/abs/../../..", "x"),
                              ("S", "docs\\..\\..\\..", "x")):
        r, sh, _ = _fetch(share, path, name)
        assert r.status_code == 400, (share, path, name, r.status_code, r.text)
        assert sh.cmds == []


def test_upload_metachars_quoted():
    sh = _Shell()

    async def go():
        orig = host_runner.execute_sync
        host_runner.execute_sync = sh.execute_sync
        try:
            with tempfile.TemporaryDirectory() as tmp:
                async with api_env() as env:
                    _, hid = await _seed(env, tmp, "S", "d", "a.txt")
                    r = await env.cli.post("/api/v1/files/upload", headers=env.op, json={
                        "host_id": hid, "share_name": "S$(touch PWNED)",
                        "remote_path": "`touch PWNED`/d",
                        "local_file_path": "/tmp/$(touch PWNED).txt"})
                    assert r.status_code == 200, r.text
        finally:
            host_runner.execute_sync = orig
    asyncio.run(go())
    assert not sh.pwned
    (argv,) = sh.argv()
    assert argv[-2] == "-c" and argv[-1].startswith("cd ")
