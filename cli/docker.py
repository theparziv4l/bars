from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from rich.console import Console

from cli.config import (
    COMPOSE_FILE_DEV,
    COMPOSE_FILE_PROD,
    COMPOSE_PROJECT_NAME,
    DB_PATTERNS,
    HOST_AGENT_VENV,
    PROJECT_ROOT,
    BARS_DIR,
    REPORTS_DIR,
    STORAGE_DIR,
)

console = Console()


def _compose_file(dev: bool) -> str:
    return str(COMPOSE_FILE_DEV if dev else COMPOSE_FILE_PROD)


def _compose_cmd(dev: bool = False) -> List[str]:
    return [
        "docker", "compose",
        "-f", _compose_file(dev),
        "-p", COMPOSE_PROJECT_NAME,
    ]


def compose_up(dev: bool = False, build: bool = True, detach: bool = True) -> subprocess.Popen | int:
    cmd = _compose_cmd(dev) + ["up"]
    if build:
        cmd.append("--build")
    if detach:
        cmd.append("-d")

    if detach:
        result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
        return result.returncode
    else:
        proc = subprocess.Popen(cmd, cwd=str(PROJECT_ROOT))
        return proc


def compose_down(dev: bool = False) -> int:
    cmd = _compose_cmd(dev) + ["down"]
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    return result.returncode


def compose_build(dev: bool = False, no_cache: bool = False) -> int:
    cmd = _compose_cmd(dev) + ["build"]
    if no_cache:
        cmd.append("--no-cache")
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    return result.returncode


def compose_logs(dev: bool = False, service: Optional[str] = None, follow: bool = True) -> None:
    cmd = _compose_cmd(dev) + ["logs"]
    if follow:
        cmd.append("-f")
    cmd.append("--tail=100")
    if service:
        cmd.append(service)
    try:
        subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    except KeyboardInterrupt:
        pass


def compose_ps(dev: bool = False) -> list[dict]:
    cmd = _compose_cmd(dev) + ["ps", "--format", "json"]
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(PROJECT_ROOT))
    if result.returncode != 0:
        return []
    try:
        lines = result.stdout.strip().splitlines()
        containers = []
        for line in lines:
            line = line.strip()
            if line:
                containers.append(json.loads(line))
        return containers
    except (json.JSONDecodeError, ValueError):
        return []


def purge_docker(keep_images: bool = False) -> None:
    console.print("[bold]Purging Docker resources...[/bold]")

    for dev in (False, True):
        cmd = _compose_cmd(dev) + ["down", "-v"]
        if not keep_images:
            cmd.append("--rmi=all")
        subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True)

    console.print("  [green]Docker resources purged[/green]")


def purge_files() -> None:
    console.print("[bold]Purging local files...[/bold]")

    for pattern in DB_PATTERNS:
        for db_file in PROJECT_ROOT.rglob(pattern):
            db_file.unlink(missing_ok=True)
            console.print(f"  Deleted: {db_file.relative_to(PROJECT_ROOT)}")
    for db_file in (PROJECT_ROOT / "backend").glob("*.db*"):
        db_file.unlink(missing_ok=True)
        console.print(f"  Deleted: {db_file.relative_to(PROJECT_ROOT)}")

    for dir_path in [STORAGE_DIR, REPORTS_DIR]:
        if dir_path.exists():
            for child in dir_path.iterdir():
                if child.is_dir():
                    shutil.rmtree(child)
                else:
                    child.unlink()
            console.print(f"  Cleaned: {dir_path.relative_to(PROJECT_ROOT)}/")

    if BARS_DIR.exists():
        shutil.rmtree(BARS_DIR)
        console.print("  Deleted: .bars/")

    if HOST_AGENT_VENV.exists():
        shutil.rmtree(HOST_AGENT_VENV)
        console.print("  Deleted: host_agent/venv/")

    console.print("  [green]Local files purged[/green]")
