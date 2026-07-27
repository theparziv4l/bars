from __future__ import annotations

import os
import secrets
import signal
import subprocess
import time
from typing import Optional

from rich.console import Console

from cli.config import (
    AGENT_PORT,
    HOST_AGENT_DIR,
    HOST_AGENT_LOG,
    HOST_AGENT_MAIN,
    HOST_AGENT_REQUIREMENTS,
    HOST_AGENT_VENV,
    LOG_DIR,
    PROJECT_ROOT,
)
from cli.state import is_pid_alive, is_agent_process

console = Console()

AGENT_PYTHON = HOST_AGENT_VENV / "bin" / "python"
AGENT_PIP = HOST_AGENT_VENV / "bin" / "pip"


def ensure_agent_venv() -> None:
    if AGENT_PYTHON.exists():
        console.print("  [dim]Agent venv already exists[/dim]")
        return
    console.print("  Creating agent virtualenv...")
    subprocess.run(
        ["python3", "-m", "venv", str(HOST_AGENT_VENV)],
        check=True,
    )
    console.print("  Installing agent dependencies...")
    subprocess.run(
        [str(AGENT_PIP), "install", "-q", "-r", str(HOST_AGENT_REQUIREMENTS)],
        check=True,
    )
    console.print("  [green]Agent venv ready[/green]")


def ensure_agent_token() -> str:
    """Return the backend/agent shared secret from .env, generating it on first run.

    Called before ``docker compose up`` so the backend reads the same value via env_file.
    """
    env_path = PROJECT_ROOT / ".env"
    text = env_path.read_text() if env_path.exists() else ""
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "HOST_AGENT_TOKEN" and value.strip():
            return value.strip()

    token = secrets.token_urlsafe(32)
    with env_path.open("a") as fh:
        prefix = "" if text.endswith("\n") or not text else "\n"
        fh.write(f"{prefix}\n# Shared secret between backend and host agent (auto-generated).\n"
                 f"HOST_AGENT_TOKEN={token}\n")
    console.print("  [green]Generated HOST_AGENT_TOKEN in .env[/green]")
    return token


def start_agent_background() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_file = open(HOST_AGENT_LOG, "a")
    proc = subprocess.Popen(
        [str(AGENT_PYTHON), str(HOST_AGENT_MAIN)],
        stdout=log_file,
        stderr=log_file,
        cwd=str(HOST_AGENT_DIR),
        env={**os.environ, "HOST_AGENT_TOKEN": ensure_agent_token()},
        start_new_session=True,
    )
    time.sleep(1)
    if proc.poll() is not None:
        console.print(f"  [red]Agent failed to start (exit code {proc.returncode})[/red]")
        raise RuntimeError("Host agent failed to start")
    console.print(f"  [green]Host Agent started[/green] (PID {proc.pid})")
    return proc.pid


def stop_agent(pid: Optional[int]) -> None:
    if pid is None:
        return
    if not is_agent_process(pid):
        console.print(f"  [dim]Agent PID {pid} not running as our agent (dead or recycled); skipping[/dim]")
        return
    console.print(f"  Stopping Host Agent (PID {pid})...")
    try:
        os.kill(pid, signal.SIGTERM)
        for _ in range(10):
            time.sleep(0.5)
            if not is_pid_alive(pid):
                console.print("  [green]Agent stopped[/green]")
                return
        os.kill(pid, signal.SIGKILL)
        console.print("  [yellow]Agent killed (SIGKILL)[/yellow]")
    except OSError:
        console.print("  [dim]Agent process already gone[/dim]")


def agent_status() -> dict:
    """Return dict with keys: running, pid, http_ok."""
    from cli.state import load_state

    info = {"running": False, "pid": None, "http_ok": False}
    state = load_state()
    if state and state.agent_pid:
        info["pid"] = state.agent_pid
        info["running"] = is_agent_process(state.agent_pid)

    try:
        import urllib.error
        import urllib.request
        req = urllib.request.Request(f"http://localhost:{AGENT_PORT}/", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=3) as resp:
                info["http_ok"] = resp.status < 500
        except urllib.error.HTTPError as he:
            info["http_ok"] = he.code < 500
    except Exception:
        pass

    return info
