from __future__ import annotations

import grp
import os
import shutil
import socket
import subprocess
from dataclasses import dataclass
from typing import List

from rich.console import Console
from rich.table import Table

from cli.config import AGENT_PORT, BACKEND_PORT, FRONTEND_PORT

console = Console()


@dataclass
class CheckResult:
    name: str
    passed: bool
    message: str
    fix_hint: str = ""


def check_docker_binary() -> CheckResult:
    found = shutil.which("docker") is not None
    return CheckResult(
        name="Docker binary",
        passed=found,
        message="docker found" if found else "docker not found in PATH",
        fix_hint="Install Docker: https://docs.docker.com/engine/install/",
    )


def check_docker_daemon() -> CheckResult:
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True, timeout=10,
        )
        ok = result.returncode == 0
        return CheckResult(
            name="Docker daemon",
            passed=ok,
            message="daemon reachable" if ok else "daemon not reachable",
            fix_hint="sudo systemctl start docker",
        )
    except FileNotFoundError:
        return CheckResult(
            name="Docker daemon",
            passed=False,
            message="docker binary missing",
            fix_hint="Install Docker first",
        )
    except subprocess.TimeoutExpired:
        return CheckResult(
            name="Docker daemon",
            passed=False,
            message="docker info timed out",
            fix_hint="Check Docker daemon status: systemctl status docker",
        )


def check_docker_group() -> CheckResult:
    user = os.environ.get("USER", "")
    try:
        docker_group = grp.getgrnam("docker")
        in_group = user in docker_group.gr_mem or os.getgid() == docker_group.gr_gid
        return CheckResult(
            name="Docker rootless",
            passed=in_group,
            message=f"user '{user}' in docker group" if in_group else f"user '{user}' NOT in docker group",
            fix_hint="./bars fix-docker   (or: sudo usermod -aG docker "
                     f"{user} && newgrp docker)",
        )
    except KeyError:
        return CheckResult(
            name="Docker rootless",
            passed=False,
            message="'docker' group does not exist",
            fix_hint="./bars fix-docker   (creates the group and adds you to it)",
        )


def check_compose_v2() -> CheckResult:
    try:
        result = subprocess.run(
            ["docker", "compose", "version"],
            capture_output=True, text=True, timeout=10,
        )
        ok = result.returncode == 0
        version = result.stdout.strip() if ok else ""
        return CheckResult(
            name="Docker Compose v2",
            passed=ok,
            message=version if ok else "docker compose not available",
            fix_hint="Install docker-compose-plugin: apt install docker-compose-plugin",
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return CheckResult(
            name="Docker Compose v2",
            passed=False,
            message="docker compose command failed",
            fix_hint="Install docker-compose-plugin",
        )


def check_python3() -> CheckResult:
    found = shutil.which("python3") is not None
    return CheckResult(
        name="Python 3",
        passed=found,
        message="python3 found" if found else "python3 not in PATH",
        fix_hint="apt install python3 python3-venv",
    )


def check_port_available(port: int, name: str = "") -> CheckResult:
    label = f"Port {port}" + (f" ({name})" if name else "")
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            result = s.connect_ex(("127.0.0.1", port))
            available = result != 0
        return CheckResult(
            name=label,
            passed=available,
            message="available" if available else "already in use",
            fix_hint=f"lsof -i :{port} | kill the process or choose a different port",
        )
    except OSError:
        return CheckResult(name=label, passed=True, message="available")


def run_all_checks(check_ports: bool = True) -> tuple[bool, List[CheckResult]]:
    results: List[CheckResult] = [
        check_docker_binary(),
        check_docker_daemon(),
        check_docker_group(),
        check_compose_v2(),
        check_python3(),
    ]

    if check_ports:
        results.extend([
            check_port_available(BACKEND_PORT, "backend"),
            check_port_available(FRONTEND_PORT, "frontend"),
            check_port_available(AGENT_PORT, "host_agent"),
        ])

    table = Table(title="Prerequisite Checks", show_lines=False)
    table.add_column("Check", style="bold")
    table.add_column("Status", justify="center")
    table.add_column("Details")

    all_passed = True
    for r in results:
        status = "[green]PASS[/green]" if r.passed else "[red]FAIL[/red]"
        detail = r.message
        if not r.passed:
            all_passed = False
            detail += f"\n  [dim]Fix: {r.fix_hint}[/dim]"
        table.add_row(r.name, status, detail)

    console.print(table)
    return all_passed, results
