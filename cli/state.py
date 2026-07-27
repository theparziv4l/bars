from __future__ import annotations

import json
import os
import signal
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Optional

from cli.config import BARS_DIR, STATE_FILE


@dataclass
class State:
    agent_pid: Optional[int] = None
    compose_project: str = "bars"
    compose_file: str = "docker-compose.yml"
    started_at: Optional[str] = None
    dev_mode: bool = False


def ensure_dirs() -> None:
    BARS_DIR.mkdir(parents=True, exist_ok=True)
    (BARS_DIR / "logs").mkdir(exist_ok=True)


def save_state(state: State) -> None:
    ensure_dirs()
    STATE_FILE.write_text(json.dumps(asdict(state), indent=2))


def load_state() -> Optional[State]:
    if not STATE_FILE.exists():
        return None
    try:
        data = json.loads(STATE_FILE.read_text())
        return State(**{k: v for k, v in data.items() if k in State.__dataclass_fields__})
    except (json.JSONDecodeError, TypeError):
        return None


def clear_state() -> None:
    if STATE_FILE.exists():
        STATE_FILE.unlink()


def is_pid_alive(pid) -> bool:
    # Guard non-int / non-positive PIDs (corrupt state) — os.kill("x", 0) raises
    # TypeError and pid<=0 targets a process group, both of which we must never do.
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (OSError, ProcessLookupError):
        return False


def is_agent_process(pid) -> bool:
    """True only if ``pid`` is alive AND its cmdline looks like our host agent.

    Guards against a recycled PID (after a reboot without a clean stop) that now
    belongs to an unrelated process — we must not SIGTERM/SIGKILL a stranger.
    """
    if not is_pid_alive(pid):
        return False
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            cmdline = fh.read().replace(b"\x00", b" ").decode("utf-8", "replace")
    except OSError:
        # /proc unavailable (non-Linux) — fall back to liveness only.
        return True
    return "main.py" in cmdline or "host_agent" in cmdline


def is_running() -> bool:
    state = load_state()
    if state is None:
        return False
    if state.agent_pid and is_agent_process(state.agent_pid):
        return True
    return False
