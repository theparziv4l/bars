"""Share discovery parser for various pentesting tools.

Parses output from SMBMap, NetExec/CrackMapExec, smbclient, 
showmount (NFS), and FTP directory listings.
"""
import re
from typing import Any, Dict, List, Optional, Tuple
from dataclasses import dataclass, field
from enum import Enum

from app.core.service_parser import strip_ansi


class ShareType(str, Enum):
    """Type of network share."""
    SMB = "smb"
    NFS = "nfs"
    FTP = "ftp"
    UNKNOWN = "unknown"


class AccessLevel(str, Enum):
    """Access permissions for a share."""
    NO_ACCESS = "no_access"
    READ_ONLY = "read"
    READ_WRITE = "read_write"
    UNKNOWN = "unknown"


@dataclass
class ShareInfo:
    """Discovered network share information."""
    share_name: str
    share_type: ShareType
    access_level: AccessLevel = AccessLevel.UNKNOWN
    comment: str = ""
    host_ip: str = ""
    allowed_hosts: str = ""  # For NFS exports
    extra_data: Dict[str, Any] = field(default_factory=dict)


@dataclass
class FileEntry:
    """File or directory entry within a share."""
    name: str
    file_type: str  # "file", "directory", "link"
    size: Optional[int] = None
    permissions: str = ""
    owner: str = ""
    group: str = ""
    date: str = ""
    is_hidden: bool = False
    is_readable: bool = True
    is_writable: bool = False
    extra_data: Dict[str, Any] = field(default_factory=dict)


class ShareParser:
    """
    Unified parser for share discovery tool outputs.
    
    Supports:
    - SMBMap (share enumeration)
    - NetExec/CrackMapExec (smb --shares, spider)
    - smbclient (directory listings)
    - showmount (NFS exports)
    - FTP directory listings
    """
    
    # Tool detection patterns
    TOOL_SIGNATURES = {
        "smbmap": [
            r"\[\+\]\s+IP:\s+[\d\.]+:\d+",
            r"Disk\s+Permissions\s+Comment",
        ],
        "netexec": [
            r"(SMB|NXC)\s+[\d\.]+\s+\d+\s+\w+",
            r"CRACKMAPEXEC",
            r"nxc smb",
        ],
        "enum4linux": [
            r"enum4linux",
            r"Sharename\s+Type\s+Comment",
            r"\[\+\]\s+Enumerating",
        ],
        "smbclient": [
            r"^\s+\S+\s+[DAHRSN]+\s+\d+\s+\w+\s+\w+\s+\d+",
            r"smb:\s*\\\\",
        ],
        "showmount": [
            r"Export list for",
            r"^/\S+\s+",
        ],
        "ftp": [
            r"^[d-][rwx-]{9}",
            r"^total\s+\d+",
        ],
    }
    
    def __init__(self):
        self._compiled_patterns: Dict[str, List[re.Pattern]] = {}
        for tool, patterns in self.TOOL_SIGNATURES.items():
            self._compiled_patterns[tool] = [
                re.compile(p, re.MULTILINE | re.IGNORECASE) for p in patterns
            ]
    
    def detect_tool(self, output: str) -> str:
        """
        Auto-detect which tool produced the output.
        
        Returns tool name or "unknown".
        """
        for tool, patterns in self._compiled_patterns.items():
            matches = sum(1 for p in patterns if p.search(output))
            if matches >= 1:
                return tool
        return "unknown"
    
    def parse_shares(
        self, 
        output: str, 
        tool: Optional[str] = None,
        host_ip: str = ""
    ) -> List[ShareInfo]:
        """
        Parse share discovery output into structured data.
        
        Args:
            output: Raw tool output
            tool: Tool name (auto-detected if None)
            host_ip: Target host IP for context
            
        Returns:
            List of discovered shares
        """
        # Strip ANSI before detection AND parsing — colored netexec/smbmap output
        # otherwise fails the ^SMB anchors and drops every share/host silently.
        output = strip_ansi(output)
        if not tool:
            tool = self.detect_tool(output)

        parsers = {
            "smbmap": self._parse_smbmap,
            "netexec": self._parse_netexec,
            "enum4linux": self._parse_enum4linux,
            "smbclient": self._parse_smbclient_shares,
            "showmount": self._parse_showmount,
            "ftp": lambda o: [],  # FTP doesn't have share concept
        }
        
        parser = parsers.get(tool, lambda o: [])
        shares = parser(output)
        
        # Add host IP to all shares
        for share in shares:
            if not share.host_ip:
                share.host_ip = host_ip
        
        return shares
    
    def parse_files(
        self,
        output: str,
        share_name: str = "",
        tool: Optional[str] = None
    ) -> List[FileEntry]:
        """
        Parse file listing within a share.
        
        Args:
            output: Raw directory listing output
            share_name: Name of the share being listed
            tool: Tool name (auto-detected if None)
            
        Returns:
            List of file entries
        """
        output = strip_ansi(output)
        if not tool:
            tool = self.detect_tool(output)

        parsers = {
            "smbclient": self._parse_smbclient_files,
            "ftp": self._parse_ftp_files,
            "nfs": self._parse_ls_files,
        }
        
        # Default to ls-style parsing
        parser = parsers.get(tool, self._parse_ls_files)
        return parser(output)
    
    def _parse_smbmap(self, output: str) -> List[ShareInfo]:
        """
        Parse SMBMap output.
        
        Example format:
        [+] IP: 192.168.1.1:445	Name: DC01  Status: Authenticated
            Disk                Permissions     Comment
            ----                -----------     -------
            ADMIN$              NO ACCESS       Remote Admin
            C$                  NO ACCESS       Default share
            IPC$                READ ONLY       Remote IPC
            ./IPC$
            fr--r--r--     3 Sun Dec 31 19:03:58 1600	InitShutdown
            ...
            test                READ, WRITE
            ./test
            dr--r--r--     0 Sun Feb  8 18:18:02 2026	.
            ...
        
        The shares are listed BEFORE the ./ShareName file listings.
        We need to stop parsing shares when we see the first "./ShareName" line.
        """
        shares = []
        current_ip = ""
        current_host = ""
        in_share_section = False
        
        lines = output.split('\n')
        i = 0
        
        while i < len(lines):
            line = lines[i]
            
            # Detect IP header
            ip_match = re.search(r'\[\+\]\s+IP:\s+([\d\.]+):\d+\s+Name:\s*(\S*)', line)
            if ip_match:
                current_ip = ip_match.group(1)
                current_host = ip_match.group(2)
                i += 1
                continue
            
            # Detect header row - start of shares section
            if re.search(r'Disk\s+Permissions\s+Comment', line, re.IGNORECASE):
                in_share_section = True
                i += 1
                continue
            
            # Skip separator line (----  -----------  -------)
            if re.match(r'\s*-+\s+-+', line):
                i += 1
                continue
            
            # A "./ShareName" line means we've left this host's share table and
            # entered its file listings. Leave the share section but keep scanning —
            # a multi-host run has more "[+] IP:" blocks whose shares we still want.
            if re.match(r'\s*\.\/', line):
                in_share_section = False
                i += 1
                continue
            
            # Parse share line - must be in share section
            # Share names don't start with file permission patterns ([df]r--r--r--)
            if in_share_section and line.strip():
                # Skip lines that look like file entries (have permission patterns at start)
                # File permission patterns: [fdr][r-][w-][x-]... or similar
                if re.match(r'\s+[fdr][rwx-]{2,}', line):
                    i += 1
                    continue
                
                # Valid share line pattern:
                # ShareName followed by permissions (NO ACCESS, READ ONLY, READ, WRITE, etc.)
                # Use flexible whitespace (tabs or spaces)
                # Name may contain spaces ("File Server"); columns are separated by
                # 2+ spaces/tabs. Non-greedy name up to the perms/comment column gap.
                share_match = re.match(
                    r'^\s+(.+?)(?:\s{2,}(NO ACCESS|READ ONLY|READ,?\s*WRITE|WRITE ONLY|READ|WRITE))?(?:\s{2,}(.*))?\s*$',
                    line,
                    re.IGNORECASE
                )
                if share_match:
                    name = share_match.group(1)
                    perms = (share_match.group(2) or "").upper().strip()
                    comment = (share_match.group(3) or "").strip()
                    
                    # Map permissions
                    if "NO ACCESS" in perms:
                        access = AccessLevel.NO_ACCESS
                    elif "WRITE" in perms:
                        access = AccessLevel.READ_WRITE
                    elif "READ" in perms:
                        access = AccessLevel.READ_ONLY
                    else:
                        access = AccessLevel.UNKNOWN
                    
                    shares.append(ShareInfo(
                        share_name=name,
                        share_type=ShareType.SMB,
                        access_level=access,
                        comment=comment,
                        host_ip=current_ip,
                        extra_data={"hostname": current_host}
                    ))
            
            i += 1
        
        return shares
    
    def parse_smbmap_files(self, output: str) -> Dict[str, List[FileEntry]]:
        """
        Parse SMBMap file listings from -r output.
        
        Returns a dict mapping share names to lists of files.
        
        Example format:
            ./IPC$
            fr--r--r--                3 Sun Dec 31 19:03:58 1600	InitShutdown
            ./test
            dr--r--r--                0 Sun Feb  8 18:18:02 2026	.
            fr--r--r--                0 Sun Feb  8 00:28:11 2026	test.txt
            ./test//testsub
            dr--r--r--                0 Sun Feb  8 00:28:11 2026	.
        """
        files_by_share: Dict[str, List[FileEntry]] = {}
        current_share = ""
        current_path = "/"
        
        for line in strip_ansi(output).split('\n'):
            # Detect share/path header (./ShareName or ./ShareName//path);
            # share and subdir names may contain spaces.
            path_match = re.match(r'\s*\.\/(.+?)(?:\/\/(.+))?$', line)
            if path_match:
                current_share = path_match.group(1)
                subpath = path_match.group(2)
                current_path = "/" + subpath if subpath else "/"
                if current_share not in files_by_share:
                    files_by_share[current_share] = []
                continue
            
            # Parse file entries - format: [attributes] [size] [date] [name]
            # Example: fr--r--r--                3 Sun Dec 31 19:03:58 1600	InitShutdown
            file_match = re.match(
                r'\s+([df]r[-rwx]{2}[-rwx]{2}[-rwx]{2}[-rwx]{2})\s+'  # Permissions
                r'(\d+)\s+'                                           # Size
                r'(\w+\s+\w+\s+\d+\s+\d+:\d+:\d+\s+\d+)\s+'            # Date
                r'(.+)$',                                             # Filename
                line
            )
            if file_match and current_share:
                perms = file_match.group(1)
                size = int(file_match.group(2))
                date = file_match.group(3).strip()
                name = file_match.group(4).strip()
                
                # Skip . and ..
                if name in ('.', '..'):
                    continue
                
                # Determine file type from first char
                if perms.startswith('d'):
                    file_type = "directory"
                elif perms.startswith('l'):
                    file_type = "link"
                else:
                    file_type = "file"
                
                # Parse read/write from permissions
                is_readable = 'r' in perms[1:4]  # owner read
                is_writable = 'w' in perms[1:4]  # owner write
                
                entry = FileEntry(
                    name=name,
                    file_type=file_type,
                    size=size if size > 0 else None,
                    permissions=perms,
                    date=date,
                    is_hidden=name.startswith('.'),
                    is_readable=is_readable,
                    is_writable=is_writable,
                    extra_data={"share_path": current_path}
                )
                files_by_share[current_share].append(entry)
        
        return files_by_share
    
    # ============ NetExec/CrackMapExec Parser ============
    
    def _parse_netexec(self, output: str) -> List[ShareInfo]:
        """
        Parse NetExec/CrackMapExec SMB output.
        
        Example formats:
        SMB  192.168.163.156 445    CLIENT1          [*] Enumerated shares
        SMB  192.168.163.156 445    CLIENT1          Share           Permissions     Remark
        SMB  192.168.163.156 445    CLIENT1          -----           -----------     ------
        SMB  192.168.163.156 445    CLIENT1          ADMIN$                          Remote Admin
        SMB  192.168.163.156 445    CLIENT1          C$                              Default share
        SMB  192.168.163.156 445    CLIENT1          IPC$            READ            Remote IPC
        SMB  192.168.163.156 445    CLIENT1          test            READ,WRITE
        SMB  192.168.163.156 445    CLIENT1          Users           READ
        
        Note: Lines with [*], [+], [-] are status messages, not shares.
        """
        shares = []
        in_share_section = False
        current_ip = ""
        current_hostname = ""
        
        for line in output.split('\n'):
            # Skip empty lines
            if not line.strip():
                continue
            
            # Check for SMB protocol lines
            smb_match = re.match(r'^SMB\s+([\d\.]+)\s+\d+\s+(\S+)\s+(.*)$', line)
            if not smb_match:
                continue
            
            current_ip = smb_match.group(1)
            current_hostname = smb_match.group(2)
            content = smb_match.group(3).strip()
            
            # Skip status messages: [*], [+], [-]
            if re.match(r'\[\*\]|\[\+\]|\[-\]', content):
                # Check if this is the "Enumerated shares" line that starts share section
                if "[*] Enumerated shares" in content:
                    in_share_section = True
                continue
            
            # Skip header line (Share, Permissions, Remark)
            if content.startswith("Share") and "Permissions" in content:
                in_share_section = True
                continue
            
            # Skip separator line (-----)
            if content.startswith("-----"):
                continue
            
            # Parse share entry if we're in the share section
            if in_share_section and content:
                # Pattern: ShareName [spaces] [Permissions] [spaces] [Remark]
                # ShareName is first word, Permissions is optional READ/WRITE, rest is remark
                # Name may contain spaces; columns separated by 2+ spaces. Allow the
                # space in "READ, WRITE" so a writable share isn't reported read-only.
                share_match = re.match(
                    r'^(.+?)(?:\s{2,}(READ,?\s*WRITE|READ|WRITE))?(?:\s{2,}(.*))?\s*$',
                    content,
                    re.IGNORECASE
                )
                if share_match:
                    share_name = share_match.group(1)
                    perms = (share_match.group(2) or "").upper().strip()
                    comment = (share_match.group(3) or "").strip()
                    
                    # Skip if this looks like a header/separator
                    if share_name.lower() in ('share', '-----', '----', '-----------'):
                        continue
                    
                    # Map permissions
                    if not perms:
                        access = AccessLevel.NO_ACCESS
                    elif "WRITE" in perms:
                        access = AccessLevel.READ_WRITE
                    elif "READ" in perms:
                        access = AccessLevel.READ_ONLY
                    else:
                        access = AccessLevel.UNKNOWN
                    
                    shares.append(ShareInfo(
                        share_name=share_name,
                        share_type=ShareType.SMB,
                        access_level=access,
                        comment=comment,
                        host_ip=current_ip,
                        extra_data={"hostname": current_hostname}
                    ))
        
        return shares
    
    # ============ smbclient Parser ============
    
    def _parse_smbclient_shares(self, output: str) -> List[ShareInfo]:
        """
        Parse smbclient -L output (share listing).
        
        Example:
        Sharename       Type      Comment
        ---------       ----      -------
        ADMIN$          Disk      Remote Admin
        C$              Disk      Default share
        IPC$            IPC       Remote IPC
        """
        shares = []
        
        # Name may contain spaces; require a 2+-space column gap before the type so
        # stray "Local Disk C" prose lines don't become phantom shares. Type keyword
        # is case-sensitive (smbclient/enum4linux print "Disk"/"IPC"/"Printer").
        pattern = re.compile(
            r'^\s*(.+?)\s{2,}(Disk|IPC|Printer)(?:\s{2,}(.*))?\s*$',
            re.MULTILINE
        )
        
        for match in pattern.finditer(output):
            name = match.group(1)
            share_type_str = match.group(2).lower()
            comment = (match.group(3) or "").strip()
            
            # Skip headers
            if name.lower() in ('sharename', '---------'):
                continue
            
            shares.append(ShareInfo(
                share_name=name,
                share_type=ShareType.SMB,
                access_level=AccessLevel.UNKNOWN,  # smbclient -L doesn't show perms
                comment=comment,
                extra_data={"share_class": share_type_str}
            ))
        
        return shares
    
    def _parse_smbclient_files(self, output: str) -> List[FileEntry]:
        """
        Parse smbclient directory listing.
        
        Example:
          .                                   D        0  Mon Jan 20 10:00:00 2025
          ..                                  D        0  Mon Jan 20 10:00:00 2025
          Documents                           D        0  Mon Jan 20 10:00:00 2025
          my document.txt                     A      123  Mon Jan 20 10:00:00 2025
          passwords.txt                       A      123  Mon Jan 20 10:00:00 2025
        """
        entries = []
        
        # Use \s{2,} as delimiter between filename and attributes.
        # smbclient pads columns with multiple spaces, so a single space
        # inside a filename won't be mistaken for a column separator.
        pattern = re.compile(
            r'^\s+(.+?)\s{2,}'       # Filename (may contain single spaces)
            r'([DAHRSN]+)\s+'        # Attributes (required)
            r'(\d+)\s+'              # Size
            r'(\w+\s+\w+\s+\d+\s+'   # Date start
            r'\d+:\d+:\d+\s+\d+)',   # Time and year
            re.MULTILINE
        )
        
        for match in pattern.finditer(output):
            name = match.group(1).strip()
            attrs = match.group(2) or ""
            size = int(match.group(3))
            date = match.group(4).strip()
            
            # Skip . and ..
            if name in ('.', '..'):
                continue
            
            file_type = "directory" if "D" in attrs else "file"
            
            entries.append(FileEntry(
                name=name,
                file_type=file_type,
                size=size,
                permissions=attrs,
                date=date,
                is_hidden="H" in attrs,
                is_writable=False,  # Default, smbclient doesn't show this directly
                extra_data={
                    "system": "S" in attrs,
                    "archive": "A" in attrs,
                    "readonly": "R" in attrs,
                }
            ))
        
        return entries
    
    # ============ NFS showmount Parser ============
    
    def _parse_showmount(self, output: str) -> List[ShareInfo]:
        """
        Parse showmount -e output.
        
        Example:
        Export list for 192.168.1.1:
        /home        *
        /backup      192.168.1.0/24
        /nfs/public  (everyone)
        """
        shares = []
        host_ip = ""
        
        # Extract host from header
        header_match = re.search(r'Export list for ([\d\.]+):', output)
        if header_match:
            host_ip = header_match.group(1)
        
        # Parse export lines. Client/allowed-hosts column is optional — some servers
        # print an export path on its own line.
        pattern = re.compile(r'^(/\S+)(?:\s+(.+))?\s*$', re.MULTILINE)

        for match in pattern.finditer(output):
            path = match.group(1)
            allowed = (match.group(2) or "").strip()
            
            shares.append(ShareInfo(
                share_name=path,
                share_type=ShareType.NFS,
                access_level=AccessLevel.UNKNOWN,
                allowed_hosts=allowed,
                host_ip=host_ip,
            ))
        
        return shares
    
    # ============ enum4linux Parser ============
    
    def _parse_enum4linux(self, output: str) -> List[ShareInfo]:
        """
        Parse enum4linux share enumeration output.
        
        enum4linux wraps smbclient -L and produces output like:
        
         ========================================== 
        |    Share Enumeration on 192.168.1.1       |
         ========================================== 
        
        	Sharename       Type      Comment
        	---------       ----      -------
        	ADMIN$          Disk      Remote Admin
        	C$              Disk      Default share
        	IPC$            IPC       Remote IPC
        	Users           Disk      
        """
        # enum4linux output contains the same Sharename/Type/Comment table
        # as smbclient -L, so reuse that parser
        return self._parse_smbclient_shares(output)
    
    # ============ FTP/ls Parser ============
    
    def _parse_ftp_files(self, output: str) -> List[FileEntry]:
        """Parse FTP directory listing (same as ls -la)."""
        return self._parse_ls_files(output)
    
    def _parse_ls_files(self, output: str) -> List[FileEntry]:
        """
        Parse ls -la style output.
        
        Example:
        drwxr-xr-x  2 user group   4096 Jan 20 10:00 Documents
        -rw-r--r--  1 user group    123 Jan 20 10:00 passwords.txt
        """
        entries = []
        
        pattern = re.compile(
            r'^([dlcbps-][rwxsStT-]{9})\s+'  # Permissions (incl. l/c/b/p/s entry types)
            r'(\d+)\s+'                  # Links
            r'(\S+)\s+'                  # Owner
            r'(\S+)\s+'                  # Group
            r'(\d+)\s+'                  # Size
            r'(\w+\s+\d+\s+[\d:]+)\s+'   # Date
            r'(.+)$',                    # Filename
            re.MULTILINE
        )
        
        for match in pattern.finditer(output):
            perms = match.group(1)
            owner = match.group(3)
            group = match.group(4)
            size = int(match.group(5))
            date = match.group(6)
            name = match.group(7).strip()
            
            # Skip . and ..
            if name in ('.', '..'):
                continue
            
            # Determine type
            if perms.startswith('d'):
                file_type = "directory"
            elif perms.startswith('l'):
                file_type = "link"
            else:
                file_type = "file"
            
            entries.append(FileEntry(
                name=name,
                file_type=file_type,
                size=size,
                permissions=perms,
                owner=owner,
                group=group,
                date=date,
                is_hidden=name.startswith('.'),
                is_readable=perms[1] == 'r',
                is_writable=perms[2] == 'w',
            ))
        
        return entries
    
    def to_discovered_file_data(
        self,
        entry: FileEntry,
        share_name: str,
        path: str = "/"
    ) -> Dict[str, Any]:
        """
        Convert FileEntry to data dict for DiscoveredFile model.
        
        Args:
            entry: Parsed file entry
            share_name: Name of the containing share
            path: Path within the share
            
        Returns:
            Dict suitable for DiscoveredFile creation
        """
        from app.models.file import DiscoveredFile
        
        # Check if file is interesting
        is_interesting, interest_reason = DiscoveredFile.is_interesting_file(entry.name)
        
        return {
            "share_name": share_name,
            "path": path,
            "name": entry.name,
            "file_type": entry.file_type,
            "size": entry.size,
            "permissions": {
                "raw": entry.permissions,
                "owner": entry.owner,
                "group": entry.group,
            },
            "is_readable": entry.is_readable,
            "is_writable": entry.is_writable,
            "is_interesting": is_interesting,
            "interest_reason": interest_reason,
            "extra_data": {
                **entry.extra_data,
                "date": entry.date,
                "hidden": entry.is_hidden,
            },
        }


# Singleton instance
share_parser = ShareParser()
