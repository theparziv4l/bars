"""Share parser regressions: smbmap perms, nxc share section reset, ls suffixes."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.share_parser import share_parser, AccessLevel


def test_smbmap_writable_file_without_read_bit_kept():
    out = (
        "    ./test\n"
        "    dw--w--w--                0 Sun Feb  8 18:18:02 2026\t.\n"
        "    fw--w--w--               12 Sun Feb  8 00:28:11 2026\twritable.txt\n"
        "    fr--r--r--                0 Sun Feb  8 00:28:11 2026\tro.txt\n"
    )
    files = share_parser.parse_smbmap_files(out)["test"]
    by_name = {f.name: f for f in files}
    assert set(by_name) == {"writable.txt", "ro.txt"}
    assert by_name["writable.txt"].is_writable is True
    assert by_name["ro.txt"].is_writable is False


NXC_SHARES_USERS = """\
SMB         10.0.0.5   445    DC01             [*] Windows Server 2019 (name:DC01) (domain:corp.local) (signing:True) (SMBv1:False)
SMB         10.0.0.5   445    DC01             [+] corp.local\\admin:Passw0rd!
SMB         10.0.0.5   445    DC01             [*] Enumerated shares
SMB         10.0.0.5   445    DC01             Share           Permissions     Remark
SMB         10.0.0.5   445    DC01             -----           -----------     ------
SMB         10.0.0.5   445    DC01             ADMIN$                          Remote Admin
SMB         10.0.0.5   445    DC01             IPC$            READ            Remote IPC
SMB         10.0.0.5   445    DC01             [*] Enumerated domain user(s)
SMB         10.0.0.5   445    DC01             corp.local\\Administrator                  Built-in account
SMB         10.0.0.5   445    DC01             corp.local\\krbtgt                         Key Distribution Center
SMB         10.0.0.5   445    DC01             [+] Dumping password info for domain: CORP
SMB         10.0.0.5   445    DC01             Minimum password length: 7
"""


def test_netexec_share_section_resets_after_table():
    shares = share_parser.parse_shares(NXC_SHARES_USERS, tool="netexec")
    names = [s.share_name for s in shares]
    assert names == ["ADMIN$", "IPC$"], names
    assert shares[0].access_level == AccessLevel.NO_ACCESS
    assert shares[1].access_level == AccessLevel.READ_ONLY


def test_ls_selinux_and_acl_suffix():
    out = (
        "drwxr-xr-x+  2 user group   4096 Jan 20 10:00 acl_dir\n"
        "-rw-r--r--.  1 user group    123 Jan 20 10:00 selinux.txt\n"
        "-rw-r--r--   1 user group     5 Jan 20 10:00 plain.txt\n"
    )
    files = share_parser.parse_files(out, tool="nfs")
    assert [f.name for f in files] == ["acl_dir", "selinux.txt", "plain.txt"]
    assert files[0].file_type == "directory"
