"""_process_discovered_files: ANSI, multi-host attribution, file dedup, share_name list."""
import asyncio
from types import SimpleNamespace

from sqlalchemy import select

from discovery_fixture import make_engine, seed, session
from app.core.orchestrator import task_orchestrator
from app.models.file import DiscoveredFile
from app.models.host import Host

NXC_SHARES = (
    "SMB  10.0.0.1 445  H1  [*] Enumerated shares\n"
    "SMB  10.0.0.1 445  H1  Share  Permissions  Remark\n"
    "SMB  10.0.0.1 445  H1  -----  -----------  ------\n"
    "SMB  10.0.0.1 445  H1  data   READ\n"
    "SMB  10.0.0.2 445  H2  [*] Enumerated shares\n"
    "SMB  10.0.0.2 445  H2  Share  Permissions  Remark\n"
    "SMB  10.0.0.2 445  H2  -----  -----------  ------\n"
    "SMB  10.0.0.2 445  H2  data   READ,WRITE\n"
)
NXC_COLORED = NXC_SHARES.replace("SMB ", "\x1b[1;34mSMB\x1b[0m")

SMBCLIENT_LS = (
    "  .                                   D        0  Mon Jan 20 10:00:00 2025\n"
    "  ..                                  D        0  Mon Jan 20 10:00:00 2025\n"
    "  passwords.txt                       A      123  Mon Jan 20 10:00:00 2025\n"
    "  notes.txt                           A       45  Mon Jan 20 10:00:00 2025\n"
)
LS_CMD = "smbclient //10.0.0.1/data -c ls"


def _run(coro):
    asyncio.run(coro)


async def _discover(db, ex, parsed=None):
    async with db.begin_nested():
        await task_orchestrator._process_discovered_files(db, ex, parsed or {})
    await db.commit()


async def _files(db, file_type=None):
    q = select(DiscoveredFile)
    rows = (await db.execute(q)).scalars().all()
    if file_type == "share":
        return [r for r in rows if r.file_type == "share"]
    if file_type == "file":
        return [r for r in rows if r.file_type != "share"]
    return rows


def test_multi_host_shares_split_per_host_ip(tmp_path):
    async def go():
        engine = await make_engine(tmp_path)
        async with session(engine) as db:
            proj, h1, ex = await seed(db, "nxc smb 10.0.0.0/30 --shares", NXC_SHARES, host_ip="10.0.0.1")
            h2 = Host(project_id=proj.id, ip_address="10.0.0.2")
            db.add(h2)
            await db.commit()
            await _discover(db, ex)
            shares = await _files(db, "share")
            by_host = {s.host_id: s for s in shares}
            assert set(by_host) == {h1.id, h2.id}
            assert by_host[h1.id].is_writable is False
            assert by_host[h2.id].is_writable is True
        await engine.dispose()
    _run(go())


def test_unknown_host_ip_falls_back_to_execution_host(tmp_path):
    async def go():
        engine = await make_engine(tmp_path)
        async with session(engine) as db:
            proj, h1, ex = await seed(db, "nxc smb 10.0.0.0/30 --shares", NXC_SHARES, host_ip="10.0.0.1")
            await _discover(db, ex)  # 10.0.0.2 is not a host in the project
            shares = await _files(db, "share")
            assert [s.host_id for s in shares] == [h1.id]  # deduped by (host, share_name)
        await engine.dispose()
    _run(go())


def test_ansi_colored_output_is_detected(tmp_path):
    async def go():
        engine = await make_engine(tmp_path)
        async with session(engine) as db:
            proj, h1, ex = await seed(db, "nxc smb 10.0.0.1 --shares", NXC_COLORED, host_ip="10.0.0.1")
            await _discover(db, ex)
            assert len(await _files(db, "share")) >= 1
        await engine.dispose()
    _run(go())


def test_file_listing_rerun_does_not_duplicate(tmp_path):
    async def go():
        engine = await make_engine(tmp_path)
        async with session(engine) as db:
            proj, h1, ex = await seed(db, LS_CMD, SMBCLIENT_LS, host_ip="10.0.0.1")
            await _discover(db, ex)
            await _discover(db, ex)
            names = sorted(f.name for f in await _files(db, "file"))
            assert names == ["notes.txt", "passwords.txt"]
        await engine.dispose()
    _run(go())


def test_share_name_list_value_does_not_break_insert(tmp_path):
    async def go():
        engine = await make_engine(tmp_path)
        async with session(engine) as db:
            proj, h1, ex = await seed(db, LS_CMD, SMBCLIENT_LS, host_ip="10.0.0.1")
            parsed = {"share_name": SimpleNamespace(value=["data", "other"])}
            await _discover(db, ex, parsed)
            files = await _files(db, "file")
            assert {f.share_name for f in files} == {"data"}
            assert len(files) == 2
        await engine.dispose()
    _run(go())
