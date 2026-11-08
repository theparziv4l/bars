"""Service parser for extracting structured data from tool outputs."""
import re
from typing import Dict, Any, List, Optional
from dataclasses import dataclass, field

@dataclass
class ServiceInfo:
    port: int
    protocol: str
    state: str = "open"
    name: Optional[str] = None
    product: Optional[str] = None
    version: Optional[str] = None
    extra_info: Optional[str] = None
    script_output: Dict[str, str] = field(default_factory=dict)  # NSE script name -> output text

@dataclass
class HostInfo:
    ip: str
    hostname: Optional[str] = None
    os_info: Optional[str] = None
    domain: Optional[str] = None
    extra_data: Dict[str, Any] = field(default_factory=dict)

@dataclass
class ParseResult:
    hosts: Dict[str, HostInfo] = field(default_factory=dict)
    services: Dict[str, List[ServiceInfo]] = field(default_factory=dict) # key is IP

class ServiceParser:
    """Parses output from various tools to extract Host and Service info."""

    def parse(self, command: str, output: str, default_ip: Optional[str] = None) -> ParseResult:
        """
        Main entry point. Detects tool from command and parses output.
        Returns a ParseResult object.
        """
        exes = _command_executables(command)
        result = ParseResult()

        if "nmap" in exes:
            self._parse_nmap(output, result, default_ip)
        elif "rustscan" in exes:
            self._parse_rustscan(output, result, default_ip)
        elif "masscan" in exes:
            self._parse_masscan(output, result)
        elif exes & {"netexec", "nxc", "crackmapexec", "cme"}:
            self._parse_netexec(output, result)
        elif output and self._looks_like_nmap_output(output):
            # Fallback: command may be a script/wrapper; detect Nmap from stdout (e.g. "Nmap scan report for", PORT table)
            self._parse_nmap(output, result, default_ip)

        return result

    @staticmethod
    def _looks_like_nmap_output(output: str) -> bool:
        """Return True if output appears to be Nmap scan report with port table."""
        if not output or len(output) < 50:
            return False
        return "Nmap scan report for" in output and ("PORT" in output or re.search(r"\d+/tcp\s+\w+\s+\S+", output) is not None)

    def _parse_nmap(self, output: str, result: ParseResult, default_ip: Optional[str] = None):
        """
        Parses Nmap output. 
        Supports standard -oN or stdout.
        Regex matches:
        - Host: "Nmap scan report for <hostname> (<ip>)"
        - PORT STATE SERVICE VERSION
        - 80/tcp open http Apache httpd 2.4.41
        """
        current_ip = default_ip
        if current_ip and current_ip not in result.hosts:
             result.hosts[current_ip] = HostInfo(ip=current_ip)

        
        # Regex (compiled for performance if reused, but here for clarity)
        # Match "Nmap scan report for domain.com (192.168.1.1)" or "Nmap scan report for 192.168.1.1"
        # IP group accepts IPv4 and IPv6 literals (hex + colons), so 2001:db8::1
        # isn't truncated to "2001" and its ports mis-attributed.
        # Hostname-only form ("Nmap scan report for host.name", no "(ip)") has no IP:
        # group 4 captures it so it isn't truncated to hex-looking chars ("dc01.").
        host_pattern = re.compile(
            r"Nmap scan report for (?:([\w\.-]+) \(([0-9a-fA-F:.]+)\)|([0-9a-fA-F:.]+)(?!\S)|([\w\.-]+))")
        
        # Match port line: 80/tcp open http Apache httpd 2.4.41 ((Ubuntu))
        # Group 1: Port, 2: Proto, 3: State, 4: Service, 5: Version/Product
        # State group allows "open|filtered"; service column is optional so bare
        # "9999/tcp open" and UDP "68/udp open|filtered" lines aren't dropped.
        port_pattern = re.compile(r"^(\d+)\/(tcp|udp)\s+([\w|]+)(?:\s+(\S+))?\s*(.*)$")
        
        # Match OS line: "OS details: Linux 4.15 - 5.6" or "Service Info: OS: Windows; CPE: ..."
        os_pattern = re.compile(r"OS details:\s*(.+)")
        # Nmap prints "Service Info: OS: Windows; ..." (and sometimes "Host: x; OS: y").
        # Case-insensitive and skip any leading "Host:" so OS is captured on -sV scans.
        service_info_pattern = re.compile(r"Service Info:.*?OS:\s*([^;]+)", re.IGNORECASE)

        # NSE script output: lines starting with | belong to the last service for current_ip
        current_service_list: Optional[List[ServiceInfo]] = None
        current_script_name: Optional[str] = None
        current_script_lines: List[str] = []

        def flush_script():
            nonlocal current_script_name, current_script_lines, current_service_list
            if current_script_name and current_script_lines and current_service_list:
                text = "\n".join(current_script_lines).strip()
                if text:
                    current_service_list[-1].script_output[current_script_name] = text
            current_script_name = None
            current_script_lines = []

        lines = output.splitlines()
        for line in lines:
            raw_line = line
            line = strip_ansi(line).strip()
            
            # Host Detection
            host_match = host_pattern.search(line)
            if host_match:
                flush_script()
                current_service_list = None
                if host_match.group(1):
                    hostname = host_match.group(1)
                    current_ip = host_match.group(2)
                elif host_match.group(3):
                    hostname = None
                    current_ip = host_match.group(3)
                else:
                    # hostname only: attribute to the target we were given, if any
                    hostname = host_match.group(4)
                    current_ip = default_ip

                if current_ip:
                     if current_ip not in result.hosts:
                         result.hosts[current_ip] = HostInfo(ip=current_ip)
                     if hostname:
                         result.hosts[current_ip].hostname = hostname
                continue
            
            if not current_ip:
                continue

            # Service Detection (port line); use search() so leading whitespace/prefixes still match
            port_match = port_pattern.search(line)
            if port_match:
                flush_script()
                port = int(port_match.group(1))
                proto = port_match.group(2)
                state = port_match.group(3)
                service_name = (port_match.group(4) or "").strip()
                version_info = port_match.group(5).strip()
                if not service_name:
                    service_name = "unknown"

                result.services.setdefault(current_ip, []).append(ServiceInfo(
                    port=port,
                    protocol=proto,
                    state=state,
                    name=service_name,
                    version=version_info if version_info else None,
                ))
                current_service_list = result.services[current_ip]
                continue

            # NSE script output: | script-name: ... or |  ... or |_ ...
            if line.startswith("|") and current_service_list:
                rest = line[1:].strip()
                if rest.startswith("_"):
                    rest = rest[1:].strip()
                    if current_script_name:
                        current_script_lines.append(rest)
                    flush_script()
                elif ":" in rest:
                    flush_script()
                    idx = rest.index(":")
                    current_script_name = rest[:idx].strip()
                    current_script_lines = [rest[idx + 1 :].strip()] if rest[idx + 1 :].strip() else []
                else:
                    if current_script_name:
                        current_script_lines.append(rest)
                continue

            # OS Detection
            os_match = os_pattern.search(line)
            if os_match:
                result.hosts[current_ip].os_info = os_match.group(1)
            
            si_match = service_info_pattern.search(line)
            if si_match:
                if not result.hosts[current_ip].os_info:
                     result.hosts[current_ip].os_info = si_match.group(1)

        flush_script()

    def _parse_rustscan(self, output: str, result: ParseResult, default_ip: Optional[str] = None):
        """
        Parses Rustscan output. Handles both:
        - Greppable: "192.168.1.1 -> [22,80,443]"
        - Default:   "Open 192.168.1.1:22" lines, usually followed by a full embedded
          Nmap report when run as `rustscan -a <ip> -- -sV` (the common case).
        """
        clean = strip_ansi(output)

        def add(ip, port):
            if ip not in result.hosts:
                result.hosts[ip] = HostInfo(ip=ip)
            result.services.setdefault(ip, []).append(
                ServiceInfo(port=port, protocol="tcp", state="open", name="unknown")
            )

        # Greppable "IP -> [ports]"
        for match in re.finditer(r"(\d{1,3}(?:\.\d{1,3}){3})\s*->\s*\[([\d,]+)\]", clean):
            ip = match.group(1)
            for p in match.group(2).split(','):
                if p.strip():
                    add(ip, int(p))

        # Default "Open <ip>:<port>" lines
        for match in re.finditer(r"^Open\s+(\d{1,3}(?:\.\d{1,3}){3}):(\d+)", clean, re.MULTILINE):
            add(match.group(1), int(match.group(2)))

        # `rustscan -- <nmap args>` appends a real Nmap report — delegate so we also
        # capture service names/versions/scripts. _merge_duplicate_services dedupes.
        if self._looks_like_nmap_output(clean):
            self._parse_nmap(output, result, default_ip)

    def _parse_masscan(self, output: str, result: ParseResult):
        """
        Parses masscan output.
        - Default list: "Discovered open port 80/tcp on 192.168.1.1"
        - Greppable -oG: "Host: 192.168.1.1 ()   Ports: 80/open/tcp//http//"
        """
        clean = strip_ansi(output)

        def add(ip, port, proto):
            if ip not in result.hosts:
                result.hosts[ip] = HostInfo(ip=ip)
            result.services.setdefault(ip, []).append(
                ServiceInfo(port=port, protocol=proto, state="open", name="unknown")
            )

        for match in re.finditer(
            r"Discovered open port (\d+)/(tcp|udp) on (\d{1,3}(?:\.\d{1,3}){3})", clean
        ):
            add(match.group(3), int(match.group(1)), match.group(2))

        for match in re.finditer(
            r"Host:\s*(\d{1,3}(?:\.\d{1,3}){3}).*?Ports:\s*(\d+)/open/(tcp|udp)", clean
        ):
            add(match.group(1), int(match.group(2)), match.group(3))

    def _parse_netexec(self, output: str, result: ParseResult):
        """
        Parses NetExec/NXC/CrackMapExec output.
        
        Example:
        SMB         192.168.163.156 445    CLIENT1          [*] Windows 10 Pro 19045 x64 (name:CLIENT1) (domain:client1) (signing:False) (SMBv1:True)
        WINRM       192.168.1.20   5985   DC01             [*] Windows Server 2019 Build 17763 (name:DC01) (domain:contoso.local)
        """
        # We look for lines starting with protocol (SMB, WINRM, MSSQL, etc.)
        # But specifically we want Host Info.
        
        # Regex Analysis:
        # Pcol    IP               Port   Hostname         Status  OS Info...
        # SMB     1.2.3.4          445    HOST             [*]     Windows...
        
        # Regex:
        # ^(\w+)\s+(\d+\.\d+\.\d+\.\d+)\s+(\d+)\s+(\S+)\s+\[\*\]\s+(.*?)(?:\(name:(\S+)\))?(?:\s+\(domain:(\S+)\))?
        # Note: (name:xxx) and (domain:xxx) might be inside the OS info part or strictly formatted.
        # NetExec output is colorized often, but raw output might be clean.
        # The example provided:
        # SMB         192.168.163.156 445    CLIENT1          [*] Windows 10 Pro 19045 x64 (name:CLIENT1) (domain:client1) (signing:False) (SMBv1:True)
        
        # Let's try a robust regex.
        regex = re.compile(
            r"^(\w+)\s+"                 # Protocol (SMB)
            r"(\d{1,3}(?:\.\d{1,3}){3})\s+" # IP
            r"(\d+)\s+"                  # Port
            r"(\S+)\s+"                  # Hostname (First column) OR sometimes it's (name:CLIENT1) later?
                                          # In CME, col 4 is usually Hostname.
            r"\[\*\]\s+"                 # Status/Success indicator
            r"(.+)"                      # The rest (OS and extra info)
        )
        
        lines = output.splitlines()
        for line in lines:
            line = strip_ansi(line).strip()
            match = regex.match(line)
            if match:
                proto = match.group(1)
                ip = match.group(2)
                port = int(match.group(3))
                hostname_col = match.group(4)
                rest = match.group(5)
                
                # Extract structured info from 'rest'
                # Windows 10 Pro 19045 x64 (name:CLIENT1) (domain:client1) ...
                
                os_info = rest
                domain = None
                full_hostname = hostname_col
                
                # Parse (name:...) and (domain:...)
                name_match = re.search(r"\(name:([^\)]+)\)", rest)
                domain_match = re.search(r"\(domain:([^\)]+)\)", rest)
                
                if name_match:
                    full_hostname = name_match.group(1) # More accurate
                    # Remove it from OS info to clean it up? Optional.
                    
                if domain_match:
                    domain = domain_match.group(1)

                # OS Info cleanup: remove the (name:...) parts
                # simplify: split by '(' and take first part? 
                # "Windows 10 Pro 19045 x64 "
                os_clean = rest.split('(')[0].strip()
                if os_clean:
                    os_info = os_clean

                # Save Host Info
                if ip not in result.hosts:
                    result.hosts[ip] = HostInfo(ip=ip)
                
                h = result.hosts[ip]
                # Only a real banner ("... (name:X) (domain:Y) ...") carries host info.
                # Other [*] lines ("Enumerated shares", ssh/ftp version banners) would
                # overwrite hostname/os_info with junk.
                if name_match:
                    h.hostname = full_hostname
                    if domain:
                        h.domain = domain
                    h.os_info = os_info

                # Check for SMB Signing
                # (signing:False) or (signing:True)
                signing_match = re.search(r"\(signing:(True|False)\)", rest, re.IGNORECASE)
                if signing_match:
                    is_signed = signing_match.group(1).lower() == 'true'
                    h.extra_data['smb_signing'] = is_signed
                
                # Save Service Info
                # Check if we already have this service?
                result.services.setdefault(ip, []).append(ServiceInfo(
                    port=port,
                    protocol="tcp", # NetExec usually TCP
                    state="open",
                    name=proto.lower(),
                    # Do NOT hard-code product="Windows"/version=os_info here: netexec
                    # runs against Linux/Samba too, and the merge step would clobber an
                    # accurate Nmap product/version. OS is already stored on the host.
                    product=None,
                    version=None,
                ))

_WRAPPERS = {"sudo", "env", "proxychains", "proxychains4", "proxychains-ng", "nohup",
             "time", "timeout", "stdbuf", "nice", "command", "exec"}
_SHELLS = ("sh", "bash", "zsh", "dash")


def _command_executables(command: str) -> set:
    """Basenames of the programs a shell command line runs.

    Quote-aware split on ``&& || ; |``; skips VAR=val assignments, wrapper words
    (sudo, proxychains, ...) with their flags, and looks inside ``sh -c '...'``.
    So ``nxc smb nmap_hosts.txt`` yields {"nxc"}, not a substring hit on "nmap".
    ponytail: wrapper flags that take a value (``sudo -u root nmap``) make the
    value look like the program; the nmap-output fallback in parse() covers that.
    """
    import os
    import shlex
    try:
        lex = shlex.shlex(command or "", posix=True, punctuation_chars=True)
        lex.whitespace_split = True
        tokens = list(lex)
    except ValueError:
        tokens = (command or "").split()
    exes: set = set()
    segment: List[str] = []
    for tok in tokens + [";"]:
        if not (tok and set(tok) <= set("();<>|&")):
            segment.append(tok)
            continue
        i = 0
        while i < len(segment):
            t = segment[i]
            if (re.match(r"^[A-Za-z_]\w*=", t) or t.startswith("-")
                    or re.fullmatch(r"\d+[smhd]?", t)
                    or os.path.basename(t).lower() in _WRAPPERS):
                i += 1
                continue
            break
        if i < len(segment):
            exe = os.path.basename(segment[i]).lower()
            if exe in _SHELLS and "-c" in segment[i + 1:]:
                j = segment.index("-c", i + 1) + 1
                if j < len(segment):
                    exes |= _command_executables(segment[j])
            else:
                exes.add(exe)
        segment = []
    return exes


def strip_ansi(source):
    """Remove ANSI escape sequences (SGR/CSI incl. private-mode '?', and OSC)."""
    return re.sub(r'\x1b\[[0-9;?]*[a-zA-Z]|\x1b\][^\x07]*\x07', '', source)

service_parser = ServiceParser()
