"""Regression tests for the tool-output parsers (Theme B fixes).

Each case is a real tool-output snippet that used to be mis-parsed. Runnable via
`python -m pytest tests/test_parsers.py` or directly `python tests/test_parsers.py`.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # backend/

from app.core.service_parser import service_parser
from app.core.share_parser import share_parser, AccessLevel


# ---------------- service_parser ----------------

def test_ipv6_host_not_truncated():
    r = service_parser.parse("nmap -6 2001:db8::1",
                             "Nmap scan report for 2001:db8::1\n22/tcp open ssh\n")
    assert "2001:db8::1" in r.hosts        # was truncated to "2001"
    assert "2001" not in r.hosts
    assert any(s.port == 22 for s in r.services.get("2001:db8::1", []))


def test_open_filtered_and_bare_open_ports_kept():
    out = ("Nmap scan report for 1.2.3.4\n"
           "68/udp open|filtered dhcpc\n"
           "161/udp open|filtered snmp\n"
           "9999/tcp open\n")
    r = service_parser.parse("nmap -sU 1.2.3.4", out)
    ports = {s.port for s in r.services["1.2.3.4"]}
    assert {68, 161, 9999} <= ports        # all were dropped before
    p68 = next(s for s in r.services["1.2.3.4"] if s.port == 68)
    assert p68.state == "open|filtered"


def test_rustscan_default_open_lines_and_nmap_delegation():
    out = ("Open 192.168.1.1:22\n"
           "Open 192.168.1.1:80\n"
           "Nmap scan report for 192.168.1.1\n"
           "Host is up.\n"
           "PORT   STATE SERVICE\n"
           "22/tcp open  ssh\n"
           "80/tcp open  http\n")
    r = service_parser.parse("rustscan -a 192.168.1.1 -- -sV", out)
    assert "192.168.1.1" in r.hosts        # was an empty host
    assert {22, 80} <= {s.port for s in r.services["192.168.1.1"]}


def test_masscan_parsed():
    out = ("Discovered open port 80/tcp on 1.2.3.4\n"
           "Discovered open port 53/udp on 1.2.3.4\n")
    r = service_parser.parse("masscan -p1-65535 1.2.3.4", out)
    assert {80, 53} <= {s.port for s in r.services["1.2.3.4"]}   # was never parsed


def test_netexec_does_not_stamp_windows_on_linux():
    out = "SMB  10.10.10.3  445  METASPLOITABLE  [*] Unix (name:METASPLOITABLE) (domain:metasploitable)"
    r = service_parser.parse("nxc smb 10.10.10.3", out)
    assert "10.10.10.3" in r.hosts
    assert all(s.product is None for s in r.services["10.10.10.3"])   # was "Windows"
    assert "unix" in (r.hosts["10.10.10.3"].os_info or "").lower()


def test_cme_alias_dispatches():
    r = service_parser.parse("cme smb 1.2.3.4",
                             "SMB  1.2.3.4  445  HOST  [*] Windows 10 (name:HOST) (domain:corp)")
    assert "1.2.3.4" in r.hosts            # cme alias was unmatched -> nothing


def test_service_info_os_case_insensitive():
    out = ("Nmap scan report for 1.2.3.4\n"
           "445/tcp open microsoft-ds\n"
           "Service Info: OS: Windows; CPE: cpe:/o:microsoft:windows\n")
    r = service_parser.parse("nmap -sV 1.2.3.4", out)
    assert "windows" in (r.hosts["1.2.3.4"].os_info or "").lower()


# ---------------- share_parser ----------------

def test_smbmap_spaced_share_names_and_perms():
    out = ("[+] IP: 192.168.1.1:445\tName: DC01  Status: Authenticated\n"
           "\tDisk                Permissions     Comment\n"
           "\t----                -----------     -------\n"
           "\tFile Server         READ ONLY       Corp files\n"
           "\ttest                READ, WRITE\n")
    shares = share_parser.parse_shares(out, host_ip="192.168.1.1")
    by_name = {s.share_name: s for s in shares}
    assert "File Server" in by_name        # spaced name was dropped
    assert by_name["File Server"].access_level == AccessLevel.READ_ONLY
    assert by_name["test"].access_level == AccessLevel.READ_WRITE


def test_smbmap_multihost_keeps_all_hosts():
    out = ("[+] IP: 10.0.0.1:445\tName: H1  Status: Authenticated\n"
           "\tDisk                Permissions     Comment\n"
           "\tShareA              READ ONLY\n"
           "\t./ShareA\n"
           "\tdr--r--r--     0 Sun Feb  8 18:18:02 2026\t.\n"
           "[+] IP: 10.0.0.2:445\tName: H2  Status: Authenticated\n"
           "\tDisk                Permissions     Comment\n"
           "\tShareB              READ ONLY\n")
    shares = share_parser.parse_shares(out)
    pairs = {(s.host_ip, s.share_name) for s in shares}
    assert ("10.0.0.1", "ShareA") in pairs
    assert ("10.0.0.2", "ShareB") in pairs   # 2nd host was dropped by `break`


def test_netexec_colored_read_write_share():
    out = ("\x1b[1mSMB\x1b[0m  192.168.1.5  445  HOST  [*] Enumerated shares\n"
           "\x1b[1mSMB\x1b[0m  192.168.1.5  445  HOST  Share           Permissions     Remark\n"
           "SMB  192.168.1.5  445  HOST  Data            READ, WRITE     data share\n")
    shares = share_parser.parse_shares(out)
    by_name = {s.share_name: s for s in shares}
    assert "Data" in by_name                 # ANSI-colored lines were dropped
    assert by_name["Data"].access_level == AccessLevel.READ_WRITE   # was READ_ONLY


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e!r}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return failed


if __name__ == "__main__":
    sys.exit(1 if _run_all() else 0)
