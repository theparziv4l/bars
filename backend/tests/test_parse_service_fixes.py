"""service_parser regressions: nxc banner handling, tool routing, nmap host/state."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.service_parser import service_parser

NXC = """\
SMB         10.0.0.5   445    DC01             [*] Windows Server 2019 Build 17763 x64 (name:DC01) (domain:corp.local) (signing:True) (SMBv1:False)
SMB         10.0.0.5   445    DC01             [+] corp.local\\admin:Passw0rd!
SMB         10.0.0.5   445    DC01             [*] Enumerated shares
"""


def test_netexec_non_banner_star_lines_do_not_override_host_info():
    r = service_parser.parse("nxc smb 10.0.0.5 -u admin -p x --shares", NXC)
    h = r.hosts["10.0.0.5"]
    assert h.hostname == "DC01"
    assert h.os_info == "Windows Server 2019 Build 17763 x64"
    assert h.domain == "corp.local"
    assert h.extra_data["smb_signing"] is True


def test_netexec_ssh_banner_gives_no_host_info():
    out = "SSH         10.0.0.9   22     10.0.0.9         [*] SSH-2.0-OpenSSH_8.9p1 Ubuntu-3\n"
    r = service_parser.parse("nxc ssh 10.0.0.9", out)
    h = r.hosts["10.0.0.9"]
    assert h.hostname is None and h.os_info is None


def test_router_matches_executable_not_substring():
    # `nxc smb nmap_hosts.txt` must go to netexec, not the nmap parser
    r = service_parser.parse("nxc smb nmap_hosts.txt", NXC)
    assert r.hosts["10.0.0.5"].hostname == "DC01"
    assert [s.name for s in r.services["10.0.0.5"]][0] == "smb"


def test_router_sees_through_wrappers():
    out = "Nmap scan report for 1.2.3.4\n22/tcp open ssh OpenSSH 8\n"
    for cmd in ("sudo nmap -sV 1.2.3.4", "proxychains4 -q nmap 1.2.3.4",
                "FOO=1 /usr/bin/nmap 1.2.3.4", "cd /tmp && nmap 1.2.3.4 | tee o.txt",
                "bash -c 'nmap 1.2.3.4'"):
        r = service_parser.parse(cmd, out)
        assert any(s.port == 22 for s in r.services.get("1.2.3.4", [])), cmd


def test_nmap_state_exposed_per_service():
    out = ("Nmap scan report for 1.2.3.4\n"
           "22/tcp open ssh\n23/tcp closed telnet\n80/tcp filtered http\n")
    r = service_parser.parse("nmap 1.2.3.4", out)
    states = {s.port: s.state for s in r.services["1.2.3.4"]}
    assert states == {22: "open", 23: "closed", 80: "filtered"}


def test_nmap_hostname_without_ip_not_truncated():
    out = "Nmap scan report for dc01.corp.local\n22/tcp open ssh\n"
    r = service_parser.parse("nmap dc01.corp.local", out, default_ip="10.0.0.5")
    assert list(r.hosts) == ["10.0.0.5"]
    assert r.hosts["10.0.0.5"].hostname == "dc01.corp.local"
    assert any(s.port == 22 for s in r.services["10.0.0.5"])
