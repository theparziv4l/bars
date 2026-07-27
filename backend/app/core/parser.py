"""Output parser for extracting structured data from CLI output."""
import re
import json
from typing import Any, Dict, List, Optional, Tuple, Callable, Awaitable
from dataclasses import dataclass, field


@dataclass 
class ParsedResult:
    """Result of parsing CLI output."""
    key: str
    value: Any
    data_type: str
    raw_matches: List[str] = field(default_factory=list)


class OutputParser:
    """
    Parser for extracting structured data from CLI tool output.
    
    Supports:
    - Named regex groups for extraction
    - Multiple match modes (first, last, all)
    - Type coercion
    - Directory structure parsing
    """
    
    # Common patterns for pentest tools
    COMMON_PATTERNS = {
        # Nmap patterns
        "nmap_open_ports": r"(\d+)/(tcp|udp)\s+open",
        "nmap_service": r"(\d+)/(tcp|udp)\s+open\s+(\S+)\s*(.*)",
        "nmap_os": r"OS details:\s*(.+)",
        "nmap_host_up": r"Host is up",
        
        # IP/Host patterns
        "ipv4": r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b",
        "ipv6": r"\b([0-9a-fA-F:]+:[0-9a-fA-F:]+)\b",
        "hostname": r"\b([a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?(?:\.[a-zA-Z]{2,})+)\b",
        
        # Credential patterns
        "username": r"(?:user(?:name)?|login)[\s:=]+(\S+)",
        "password": r"(?:pass(?:word)?|pwd)[\s:=]+(\S+)",
        
        # SMB/Share patterns
        "smb_share": r"^\s*(\S+)\s+Disk",
        "smb_file": r"^\s+(.+?)\s+([A-Z]+)?\s+(\d+)\s+\w+\s+\w+\s+\d+",
        
        # Web patterns
        "url": r"https?://[^\s<>\"']+",
        "directory": r"(?:Found|Dir|directory)[:\s]+(/[^\s]+)",
        
        # Vulnerability indicators
        "vulnerable": r"(?:VULNERABLE|CVE-\d{4}-\d+|exploit)",
    }
    
    # File type patterns for SMB/NFS parsing
    FILE_PATTERNS = {
        "directory": r"^\s*\.?\s+D\s+",
        "file": r"^\s*\.?\s+[ANR]\s+",
    }
    
    def __init__(self):
        """Initialize parser."""
        self._compiled_patterns: Dict[str, re.Pattern] = {}
    
    def _get_pattern(self, pattern: str) -> re.Pattern:
        """Get compiled regex pattern, caching for performance."""
        if pattern not in self._compiled_patterns:
            try:
                self._compiled_patterns[pattern] = re.compile(pattern, re.MULTILINE | re.IGNORECASE)
            except re.error as e:
                raise ValueError(f"Invalid regex pattern: {pattern}") from e
        return self._compiled_patterns[pattern]
    
    async def parse_output(
        self,
        output: str,
        patterns: Dict[str, str],
        mode: str = "all",
        cwd: Optional[str] = None,
        command_executor: Optional[Callable[[str, Optional[str]], Awaitable[Dict[str, Any]]]] = None
    ) -> Dict[str, ParsedResult]:
        """
        Parse output using provided regex patterns.
        
        Args:
            output: CLI output string
            patterns: Dict of key -> regex pattern
            mode: "first", "last", or "all" matches
            cwd: Working directory for command-based extraction
            command_executor: Async callback (cmd, cwd) -> {stdout, stderr, exit_code}
        
        Returns:
            Dict of key -> ParsedResult
        """
        results = {}
        
        for key, pattern in patterns.items():
            if pattern.startswith("cmd:"):
                # Command-based extraction
                cmd_to_run = pattern[4:]
                try:
                    value = ""
                    if command_executor:
                        # Use provided executor (e.g. HostRunner)
                        result = await command_executor(cmd_to_run, cwd)
                        if result.get("exit_code") == 0:
                            value = result.get("stdout", "").strip()
                        else:
                            value = f"Extraction Failed: {result.get('stderr', '').strip()}"
                    else:
                        import asyncio as _asyncio
                        proc = await _asyncio.create_subprocess_shell(
                            cmd_to_run,
                            stdin=_asyncio.subprocess.PIPE,
                            stdout=_asyncio.subprocess.PIPE,
                            stderr=_asyncio.subprocess.PIPE,
                            cwd=cwd,
                        )
                        try:
                            stdout, stderr = await _asyncio.wait_for(
                                proc.communicate(input=output.encode() if output else None),
                                timeout=5,
                            )
                        except _asyncio.TimeoutError:
                            proc.kill()
                            await proc.wait()
                            value = "Extraction Failed: timeout"
                            stdout = stderr = None
                        if stdout is not None:
                            if proc.returncode == 0:
                                value = stdout.decode("utf-8", errors="replace").strip()
                            else:
                                value = f"Extraction Failed: {stderr.decode('utf-8', errors='replace').strip()}"
                    
                    data_type = self._infer_type(value)
                    results[key] = ParsedResult(
                        key=key,
                        value=value,
                        data_type=data_type,
                        raw_matches=[value]
                    )
                except Exception as e:
                    results[key] = ParsedResult(
                        key=key,
                        value=f"Error: {str(e)}",
                        data_type="error",
                    )
                continue

            # Regex extraction
            compiled = self._get_pattern(pattern)
            matches = compiled.findall(output)
            
            if not matches:
                continue
            
            # findall yields a tuple for multi-group patterns; pick the first
            # non-empty group so injected values are usable strings, not "('80', 'tcp')".
            raw_matches = [
                m if isinstance(m, str)
                else (m[0] if len(m) == 1 else next((g for g in m if g), m[0]))
                for m in matches
            ]
            
            if mode == "first":
                value = raw_matches[0]
            elif mode == "last":
                value = raw_matches[-1]
            else:  # all
                value = raw_matches
            
            # Infer data type
            data_type = self._infer_type(value)
            
            results[key] = ParsedResult(
                key=key,
                value=value,
                data_type=data_type,
                raw_matches=[str(m) for m in raw_matches]
            )
        
        return results
    
    async def parse_with_common_patterns(
        self,
        output: str,
        pattern_names: Optional[List[str]] = None,
        cwd: Optional[str] = None,
        command_executor: Optional[Callable[[str, Optional[str]], Awaitable[Dict[str, Any]]]] = None
    ) -> Dict[str, ParsedResult]:
        """
        Parse output using common built-in patterns.
        
        Args:
            output: CLI output string
            pattern_names: Specific patterns to use (None = all)
            cwd: Working directory
            command_executor: Async callback
        
        Returns:
            Dict of pattern_name -> ParsedResult
        """
        patterns = {}
        
        if pattern_names:
            patterns = {k: v for k, v in self.COMMON_PATTERNS.items() if k in pattern_names}
        else:
            patterns = self.COMMON_PATTERNS.copy()
        
        return await self.parse_output(output, patterns, cwd=cwd, command_executor=command_executor)
    
    def parse_directory_listing(
        self,
        output: str,
        format_type: str = "smbclient"
    ) -> List[Dict[str, Any]]:
        """
        Parse directory listing output into structured file data.
        
        Supports:
        - smbclient output
        - ls -la output
        - NFS listing
        
        Args:
            output: Directory listing output
            format_type: Parser to use (smbclient, ls, nfs)
        
        Returns:
            List of file/directory entries
        """
        if format_type == "smbclient":
            return self._parse_smbclient_listing(output)
        elif format_type == "ls":
            return self._parse_ls_listing(output)
        else:
            return self._parse_generic_listing(output)
    
    def _parse_smbclient_listing(self, output: str) -> List[Dict[str, Any]]:
        """Parse smbclient directory listing."""
        entries = []
        
        # Pattern for smbclient output
        # "  .                                   D        0  Mon Jan 20 10:00:00 2025"
        pattern = re.compile(
            r"^\s+(\S+)\s+"          # Filename
            r"([DAHRSN]+)?\s*"       # Attributes (optional)
            r"(\d+)\s+"              # Size
            r"(\w+\s+\w+\s+\d+\s+"   # Date start
            r"\d+:\d+:\d+\s+\d+)",   # Time and year
            re.MULTILINE
        )
        
        for match in pattern.finditer(output):
            name = match.group(1)
            attrs = match.group(2) or ""
            size = int(match.group(3))
            date_str = match.group(4)
            
            # Skip . and ..
            if name in (".", ".."):
                continue
            
            entry = {
                "name": name,
                "type": "directory" if "D" in attrs else "file",
                "size": size,
                "date": date_str.strip(),
                "attributes": attrs,
                "hidden": "H" in attrs,
                "readonly": "R" in attrs,
                "system": "S" in attrs,
            }
            entries.append(entry)
        
        return entries
    
    def _parse_ls_listing(self, output: str) -> List[Dict[str, Any]]:
        """Parse ls -la style output."""
        entries = []
        
        # Pattern for ls -la output
        # "-rw-r--r-- 1 user group 1234 Jan 20 10:00 filename"
        pattern = re.compile(
            r"^([drwxlsS-]{10})\s+"  # Permissions
            r"(\d+)\s+"               # Links
            r"(\S+)\s+"               # Owner
            r"(\S+)\s+"               # Group
            r"(\d+)\s+"               # Size
            r"(\w+\s+\d+\s+[\d:]+)\s+"  # Date
            r"(.+)$",                 # Filename
            re.MULTILINE
        )
        
        for match in pattern.finditer(output):
            perms = match.group(1)
            owner = match.group(3)
            group = match.group(4)
            size = int(match.group(5))
            date_str = match.group(6)
            name = match.group(7)
            
            # Skip . and ..
            if name in (".", ".."):
                continue
            
            file_type = "directory" if perms.startswith("d") else "link" if perms.startswith("l") else "file"
            
            entry = {
                "name": name,
                "type": file_type,
                "size": size,
                "date": date_str.strip(),
                "permissions": perms,
                "owner": owner,
                "group": group,
                "readable": perms[1] == "r",
                "writable": perms[2] == "w",
                "executable": perms[3] == "x",
            }
            entries.append(entry)
        
        return entries
    
    def _parse_generic_listing(self, output: str) -> List[Dict[str, Any]]:
        """Parse generic directory listing."""
        entries = []
        
        for line in output.strip().split('\n'):
            line = line.strip()
            if not line or line in (".", ".."):
                continue
            
            # Try to detect if it's a directory
            is_dir = line.endswith('/') or '<DIR>' in line.upper()
            
            entry = {
                "name": line.rstrip('/'),
                "type": "directory" if is_dir else "file",
                "size": None,
            }
            entries.append(entry)
        
        return entries
    
    def detect_alerts(
        self,
        output: str,
        alert_patterns: Dict[str, List[str]]
    ) -> List[Dict[str, Any]]:
        """
        Detect alert patterns in output.
        
        Args:
            output: CLI output string
            alert_patterns: Dict of severity -> list of patterns
                Example: {"critical": ["VULNERABLE"], "warning": ["weak"]}
        
        Returns:
            List of triggered alerts with severity and matched text
        """
        alerts = []
        
        for severity, patterns in alert_patterns.items():
            for pattern in patterns:
                compiled = self._get_pattern(pattern)
                matches = compiled.findall(output)
                
                if matches:
                    for match in matches:
                        matched_text = match if isinstance(match, str) else match[0]
                        alerts.append({
                            "severity": severity,
                            "pattern": pattern,
                            "matched_text": matched_text,
                            "line": self._find_line_containing(output, matched_text)
                        })
        
        return alerts
    
    def _find_line_containing(self, text: str, substring: str) -> Optional[str]:
        """Find the line containing a substring."""
        for line in text.split('\n'):
            if substring in line:
                return line.strip()
        return None
    
    def _infer_type(self, value: Any) -> str:
        """Infer data type from value."""
        if isinstance(value, list):
            return "list"
        elif isinstance(value, tuple):
            return "tuple"
        elif isinstance(value, str):
            # Check if it's a number
            if value.isdigit():
                return "int"
            try:
                float(value)
                return "float"
            except ValueError:
                pass
            return "string"
        else:
            return type(value).__name__
    
    def to_json(self, results: Dict[str, ParsedResult]) -> Dict[str, Any]:
        """Convert ParsedResult dict to JSON-serializable dict."""
        return {
            key: {
                "value": result.value,
                "data_type": result.data_type,
                "match_count": len(result.raw_matches)
            }
            for key, result in results.items()
        }


# Singleton instance
output_parser = OutputParser()
