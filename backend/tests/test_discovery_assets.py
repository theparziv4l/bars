"""_process_asset_discovery: savepoint safety, NSE merge, tags, host attribution, races."""
import asyncio

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from discovery_fixture import Recorder, make_engine, seed, session
from app.core.orchestrator import task_orchestrator
import app.core.orchestrator as orch_mod
from app.models.host import Host, Service

NMAP = """Nmap scan report for dc01 (10.0.0.5)
Host is up (0.001s latency).
PORT    STATE    SERVICE      VERSION
445/tcp filtered microsoft-ds
80/tcp  open     http         nginx 1.18
| http-title: go to 10.0.0.9
|_Requested resource was /login
"""


def _run(coro):
    asyncio.run(coro)


async def _discover(db, ex):
    async with db.begin_nested():
        await task_orchestrator._process_asset_discovery(db, ex)
    await db.commit()


async def _hosts(db, project_id, live_only=False):
    q = select(Host).where(Host.project_id == project_id)
    if live_only:
        q = q.where(Host.deleted_at.is_(None))
    return (await db.execute(q)).scalars().all()


def test_generic_ipv4_discovery_runs_after_service_parse(tmp_path):
    async def go():
        engine = await make_engine(tmp_path)
        async with session(engine) as db:
            proj, host, ex = await seed(db, "nmap -sV 10.0.0.5", NMAP, host_ip="10.0.0.5")
            await _discover(db, ex)
            ips = {h.ip_address for h in await _hosts(db, proj.id)}
            assert "10.0.0.9" in ips
        await engine.dispose()
    _run(go())


def test_nse_script_output_merged_into_existing_service(tmp_path):
    async def go():
        engine = await make_engine(tmp_path)
        async with session(engine) as db:
            proj, host, ex = await seed(db, "nmap -sV 10.0.0.5", NMAP, host_ip="10.0.0.5")
            db.add(Service(host_id=host.id, port=80, protocol="tcp", state="open", name="http",
                           extra_data={"nse_scripts": {"old-script": "keep"}}))
            await db.commit()
            await _discover(db, ex)
        async with session(engine) as db2:
            svc = (await db2.execute(select(Service).where(Service.port == 80))).scalar_one()
            scripts = svc.extra_data["nse_scripts"]
            assert scripts.get("old-script") == "keep"
            assert "go to 10.0.0.9" in scripts.get("http-title", "")
        await engine.dispose()
    _run(go())


def test_filtered_port_does_not_tag_host(tmp_path):
    async def go():
        engine = await make_engine(tmp_path)
        async with session(engine) as db:
            proj, host, ex = await seed(db, "nmap -sV 10.0.0.5", NMAP, host_ip="10.0.0.5")
            await _discover(db, ex)
            h = (await db.execute(select(Host).where(Host.id == host.id))).scalar_one()
            await db.refresh(h)
            assert "http" in h.tags
            assert "smb" not in h.tags
        await engine.dispose()
    _run(go())


NXC = ("SMB         10.0.0.7   445    SRV1   [*] Windows Server 2019 Build 17763 x64 "
       "(name:SRV1) (domain:corp.local) (signing:True) (SMBv1:False)\n")
NXC_NODOMAIN = "SMB         10.0.0.7   445    SRV1   [*] Windows Server 2022 (name:SRV1)\n"


def test_netexec_does_not_clobber_hostname_and_builds_fqdn(tmp_path):
    async def go():
        engine = await make_engine(tmp_path)
        async with session(engine) as db:
            proj, host, ex = await seed(
                db, "nxc smb 10.0.0.7", NXC, host_ip="10.0.0.7",
                host_kw={"hostname": "my-label"})
            await _discover(db, ex)
            h = (await db.execute(select(Host).where(Host.id == host.id))).scalar_one()
            await db.refresh(h)
            assert h.hostname == "my-label"
            assert h.fqdn == "SRV1.corp.local"
            assert h.os_info.startswith("Windows Server 2019")
        await engine.dispose()
    _run(go())


def test_netexec_netbios_domain_is_not_fqdn(tmp_path):
    async def go():
        engine = await make_engine(tmp_path)
        async with session(engine) as db:
            out = NXC.replace("corp.local", "CORP")
            proj, host, ex = await seed(db, "nxc smb 10.0.0.7", out, host_ip="10.0.0.7")
            await _discover(db, ex)
            h = (await db.execute(select(Host).where(Host.id == host.id))).scalar_one()
            await db.refresh(h)
            assert h.fqdn is None
            assert h.hostname == "SRV1"  # empty -> filled
        await engine.dispose()
    _run(go())


def test_netexec_nonbanner_line_does_not_overwrite_os(tmp_path):
    async def go():
        engine = await make_engine(tmp_path)
        async with session(engine) as db:
            proj, host, ex = await seed(
                db, "nxc smb 10.0.0.7", NXC_NODOMAIN, host_ip="10.0.0.7",
                host_kw={"os_info": "Windows"})
            await _discover(db, ex)
            h = (await db.execute(select(Host).where(Host.id == host.id))).scalar_one()
            await db.refresh(h)
            assert h.os_info == "Windows"
        await engine.dispose()
    _run(go())


def test_nmap_report_for_hostname_attributes_to_execution_host(tmp_path):
    async def go():
        engine = await make_engine(tmp_path)
        async with session(engine) as db:
            # batch child: host row created with the hostname as its address
            proj, host, ex = await seed(db, "nmap -sV dc01", NMAP, host_ip="dc01")
            await _discover(db, ex)
            svcs = (await db.execute(
                select(Service).where(Service.host_id == host.id))).scalars().all()
            assert {s.port for s in svcs} == {80, 445}
            ips = [h.ip_address for h in await _hosts(db, proj.id)]
            assert "10.0.0.5" not in ips
        await engine.dispose()
    _run(go())


def test_soft_deleted_host_not_reused_and_ip_recreated(tmp_path):
    async def go():
        from app.core.utils import utcnow
        engine = await make_engine(tmp_path, unique_host_index=True)
        async with session(engine) as db:
            proj, _h, ex = await seed(db, "nmap -sV 10.0.0.5", NMAP)
            ghost = Host(project_id=proj.id, ip_address="10.0.0.5", deleted_at=utcnow())
            db.add(ghost)
            await db.commit()
            await _discover(db, ex)
            live = [h for h in await _hosts(db, proj.id, live_only=True)
                    if h.ip_address == "10.0.0.5"]
            assert len(live) == 1 and live[0].id != ghost.id
            ghost_svcs = (await db.execute(
                select(Service).where(Service.host_id == ghost.id))).scalars().all()
            assert ghost_svcs == []
        await engine.dispose()
    _run(go())


def test_host_insert_race_reselects_existing_host(tmp_path, monkeypatch):
    """Another session inserts the same IP between our SELECT and INSERT."""
    async def go():
        engine = await make_engine(tmp_path, unique_host_index=True)
        async with session(engine) as db:
            proj, _h, ex = await seed(db, "nmap -sV 10.0.0.5", NMAP)
            pid = proj.id
            real_insert = task_orchestrator._insert_host_or_existing
            raced = {"done": False}

            async def racing_insert(db_, project_id, ip, method):
                if not raced["done"] and ip == "10.0.0.5":
                    raced["done"] = True
                    # A concurrent execution committed the same IP after our SELECT.
                    await db_.execute(text(
                        "INSERT INTO hosts (public_id, project_id, ip_address, status, extra_data,"
                        " tags, excluded, discovered_at, updated_at) VALUES ('race-1', :pid,"
                        " '10.0.0.5', 'unknown', '{}', '[]', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                    ), {"pid": project_id})
                return await real_insert(db_, project_id, ip, method)

            monkeypatch.setattr(task_orchestrator, "_insert_host_or_existing", racing_insert)
            await _discover(db, ex)
            live = [h for h in await _hosts(db, pid, live_only=True) if h.ip_address == "10.0.0.5"]
            assert len(live) == 1
            svcs = (await db.execute(
                select(Service).where(Service.host_id == live[0].id))).scalars().all()
            assert {s.port for s in svcs} == {80, 445}
        await engine.dispose()
    _run(go())


def test_host_notifications_deferred_until_commit(tmp_path, monkeypatch):
    async def go():
        engine = await make_engine(tmp_path)
        rec = Recorder()
        monkeypatch.setattr(orch_mod.notification_manager, "send_project_update", rec)
        async with session(engine) as db:
            proj, _h, ex = await seed(db, "nmap -sV 10.0.0.5", NMAP)
            async with db.begin_nested():
                await task_orchestrator._process_asset_discovery(db, ex)
            await asyncio.sleep(0.01)
            assert rec.calls == []  # still inside the open transaction
            await db.commit()
            await asyncio.sleep(0.05)
            events = {c[0][0] for c in rec.calls}
            assert "host_created" in events
        await engine.dispose()
    _run(go())
