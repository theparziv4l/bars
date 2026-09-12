from __future__ import annotations

import getpass
import os
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Optional

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from cli.agent import (
    agent_status,
    ensure_agent_token,
    ensure_agent_venv,
    ensure_env_secret,
    start_agent_background,
    stop_agent,
)
from cli.checks import (
    check_compose_v2,
    check_docker_binary,
    check_docker_daemon,
    check_docker_group,
    run_all_checks,
)
from cli.config import (
    AGENT_PORT,
    BACKEND_PORT,
    FRONTEND_PORT,
    HOST_AGENT_LOG,
    PROJECT_ROOT,
    SERVICES,
)
from cli.docker import (
    compose_build,
    compose_down,
    compose_logs,
    compose_ps,
    compose_up,
    purge_docker,
    purge_files,
)
from cli.state import (
    State,
    clear_state,
    ensure_dirs,
    is_pid_alive,
    is_running,
    load_state,
    save_state,
)

app = typer.Typer(
    name="bars",
    help="Bars CLI — manage the full stack (Docker + Host Agent).",
    no_args_is_help=True,
    rich_markup_mode="rich",
)
console = Console()


@app.command()
def start(
    dev: bool = typer.Option(False, "--dev", help="Foreground mode with log streaming and hot-reload"),
):
    """Start all services (backend, frontend, host agent)."""
    console.print(Panel("[bold]Bars[/bold]", subtitle="starting..."))

    state = load_state()
    if state and state.agent_pid and is_pid_alive(state.agent_pid):
        console.print("[yellow]Services appear to be already running. Use 'stop' first or 'restart'.[/yellow]")
        raise typer.Exit(1)

    console.print("\n[bold]Running prerequisite checks...[/bold]")
    passed, _ = run_all_checks(check_ports=True)
    if not passed:
        console.print("\n[red]Prerequisite checks failed. Fix the issues above and retry.[/red]")
        raise typer.Exit(1)

    console.print("\n[bold]Setting up Host Agent...[/bold]")
    ensure_agent_venv()

    console.print("\n[bold]Starting Host Agent...[/bold]")
    agent_pid = start_agent_background()

    compose_file = "docker-compose.dev.yml" if dev else "docker-compose.yml"
    new_state = State(
        agent_pid=agent_pid,
        compose_file=compose_file,
        started_at=datetime.now().isoformat(),
        dev_mode=dev,
    )

    if dev:
        console.print(f"\n[bold]Starting Docker services (dev mode)...[/bold]")
        save_state(new_state)

        def _cleanup(sig, frame):
            console.print("\n[bold]Shutting down...[/bold]")
            stop_agent(agent_pid)
            compose_down(dev=True)
            clear_state()
            raise SystemExit(0)

        signal.signal(signal.SIGINT, _cleanup)
        signal.signal(signal.SIGTERM, _cleanup)

        try:
            proc = compose_up(dev=True, build=True, detach=False)
            if isinstance(proc, subprocess.Popen):
                proc.wait()
        except (KeyboardInterrupt, SystemExit):
            pass
        finally:
            stop_agent(agent_pid)
            compose_down(dev=True)
            clear_state()
    else:
        console.print(f"\n[bold]Starting Docker services (background)...[/bold]")
        rc = compose_up(dev=False, build=True, detach=True)
        if rc != 0:
            console.print("[red]Docker compose failed to start.[/red]")
            stop_agent(agent_pid)
            raise typer.Exit(1)

        save_state(new_state)
        console.print()
        console.print(Panel(
            f"[green]All services running![/green]\n\n"
            f"  Backend:    http://localhost:{BACKEND_PORT}\n"
            f"  Frontend:   http://localhost:{FRONTEND_PORT}\n"
            f"  Host Agent: http://localhost:{AGENT_PORT}\n\n"
            f"[dim]Use './bars logs' to view logs, './bars stop' to shut down.[/dim]",
            title="[bold green]Bars[/bold green]",
        ))


@app.command()
def stop():
    """Stop all running services."""
    console.print(Panel("[bold]Bars[/bold]", subtitle="stopping..."))

    state = load_state()
    if state is None:
        console.print("[yellow]No running state found. Attempting cleanup anyway...[/yellow]")
        compose_down(dev=False)
        compose_down(dev=True)
        return

    console.print("[bold]Stopping Host Agent...[/bold]")
    stop_agent(state.agent_pid)

    console.print("[bold]Stopping Docker services...[/bold]")
    compose_down(dev=state.dev_mode)

    clear_state()
    console.print("\n[green]All services stopped.[/green]")


@app.command()
def restart(
    dev: bool = typer.Option(False, "--dev", help="Restart in dev (foreground) mode"),
):
    """Restart all services (stop + start)."""
    state = load_state()
    if state:
        console.print("[bold]Stopping current services...[/bold]")
        stop_agent(state.agent_pid)
        compose_down(dev=state.dev_mode)
        clear_state()

    start(dev=dev)


@app.command()
def status():
    """Show status of all services."""
    table = Table(title="Service Status", show_lines=True)
    table.add_column("Service", style="bold")
    table.add_column("Status", justify="center")
    table.add_column("Port", justify="center")
    table.add_column("PID / Container")
    table.add_column("Details")

    containers = compose_ps()
    container_map = {}
    for c in containers:
        name = c.get("Service", c.get("Name", ""))
        container_map[name.lower()] = c

    for svc_name in ["backend", "frontend"]:
        c = container_map.get(svc_name, {})
        if c:
            state_str = c.get("State", "unknown")
            running = state_str == "running"
            status_display = "[green]running[/green]" if running else f"[red]{state_str}[/red]"
            cid = c.get("ID", "")[:12]
            table.add_row(
                svc_name.capitalize(),
                status_display,
                str(SERVICES[svc_name]["port"]),
                cid,
                c.get("Status", ""),
            )
        else:
            table.add_row(
                svc_name.capitalize(),
                "[red]stopped[/red]",
                str(SERVICES[svc_name]["port"]),
                "-",
                "",
            )

    a_info = agent_status()
    if a_info["running"]:
        a_status = "[green]running[/green]"
    else:
        a_status = "[red]stopped[/red]"
    a_pid = str(a_info["pid"]) if a_info["pid"] else "-"
    a_http = "HTTP OK" if a_info["http_ok"] else "HTTP unreachable"
    table.add_row("Host Agent", a_status, str(AGENT_PORT), a_pid, a_http)

    state = load_state()
    mode = ""
    if state:
        mode = "dev" if state.dev_mode else "production"
        if state.started_at:
            mode += f"  |  started: {state.started_at[:19]}"

    console.print(table)
    if mode:
        console.print(f"  [dim]Mode: {mode}[/dim]")


@app.command()
def logs(
    service: Optional[str] = typer.Argument(None, help="Service name: backend, frontend, or agent"),
    follow: bool = typer.Option(True, "--follow/--no-follow", "-f/-F", help="Follow log output"),
):
    """Tail logs from services."""
    state = load_state()
    dev = state.dev_mode if state else False

    if service == "agent":
        if not HOST_AGENT_LOG.exists():
            console.print("[yellow]No agent log file found.[/yellow]")
            raise typer.Exit(1)
        try:
            cmd = ["tail"]
            if follow:
                cmd.append("-f")
            cmd.extend(["-n", "100", str(HOST_AGENT_LOG)])
            subprocess.run(cmd)
        except KeyboardInterrupt:
            pass
    else:
        compose_logs(dev=dev, service=service, follow=follow)


@app.command()
def health():
    """Check health of all service endpoints."""
    table = Table(title="Health Check", show_lines=True)
    table.add_column("Service", style="bold")
    table.add_column("Endpoint")
    table.add_column("Status", justify="center")
    table.add_column("Response Time")

    checks = [
        ("Backend", f"http://localhost:{BACKEND_PORT}/api/v1/auth/me"),
        ("Frontend", f"http://localhost:{FRONTEND_PORT}/"),
        ("Host Agent", f"http://localhost:{AGENT_PORT}/"),
    ]

    for name, url in checks:
        try:
            t0 = time.time()
            req = urllib.request.Request(url, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    code = resp.status
            except urllib.error.HTTPError as he:
                code = he.code
            elapsed = (time.time() - t0) * 1000
            if code < 500:
                table.add_row(name, url, f"[green]{code}[/green]", f"{elapsed:.0f}ms")
            else:
                table.add_row(name, url, f"[red]{code}[/red]", f"{elapsed:.0f}ms")
        except Exception as e:
            table.add_row(name, url, "[red]DOWN[/red]", str(type(e).__name__))

    console.print(table)


@app.command()
def update(
    no_cache: bool = typer.Option(False, "--no-cache", help="Build without Docker cache"),
):
    """Rebuild Docker images."""
    state = load_state()
    dev = state.dev_mode if state else False

    console.print("[bold]Building Docker images...[/bold]")
    rc = compose_build(dev=dev, no_cache=no_cache)
    if rc != 0:
        console.print("[red]Build failed.[/red]")
        raise typer.Exit(1)

    console.print("[green]Build complete.[/green]")

    if state and is_running():
        do_restart = typer.confirm("Services are running. Restart now?")
        if do_restart:
            restart(dev=dev)


RELOGIN_HINT = (
    "[yellow]Group membership only applies to new logins.[/yellow] Log out and back in "
    "(or run [bold]newgrp docker[/bold] in this shell), then verify with [bold]docker info[/bold]."
)


def _ensure_docker_group(assume_yes: bool, indent: str = "") -> str:
    """Give this user Docker daemon access via the 'docker' group.

    Returns one of: ``ok`` (already working), ``added`` (needs a re-login),
    ``relogin`` (in the group, daemon still unreachable), ``skipped``.
    """
    user = os.environ.get("USER") or getpass.getuser()

    if check_docker_group().passed:
        if check_docker_daemon().passed:
            console.print(f"{indent}[green]User '{user}' already has Docker access.[/green]")
            return "ok"
        console.print(
            f"{indent}[yellow]User '{user}' is in the 'docker' group, but the daemon is "
            f"unreachable from this session.[/yellow]\n"
            f"{indent}Log out and back in (or run [bold]newgrp docker[/bold]) to pick up the group.\n"
            f"{indent}If it still fails, the daemon may be down: "
            "[bold]sudo systemctl start docker[/bold]."
        )
        return "relogin"

    if not assume_yes:
        console.print(
            f"{indent}[bold red]Security warning:[/bold red] members of the 'docker' group can "
            "start privileged containers and mount the host filesystem. This is equivalent to "
            f"giving '{user}' passwordless root on this machine. Only do this on a machine you "
            "control.\n"
        )
        if not typer.confirm(f"{indent}Add user '{user}' to the 'docker' group?", default=False):
            console.print(f"{indent}[dim]Skipped.[/dim]")
            return "skipped"

    console.print(f"{indent}Adding '{user}' to the 'docker' group (sudo required)...")
    rc = subprocess.run(["sudo", "groupadd", "-f", "docker"]).returncode
    if rc == 0:
        rc = subprocess.run(["sudo", "usermod", "-aG", "docker", user]).returncode
    if rc != 0:
        console.print(f"{indent}[red]Failed. Run manually: sudo usermod -aG docker {user}[/red]")
        return "skipped"

    console.print(f"{indent}[green]User '{user}' added to the 'docker' group.[/green]")
    return "added"


def _install_system_packages(assume_yes: bool) -> None:
    """apt-install whatever Docker pieces are missing. Everything else is already vendored."""
    missing = []
    if not check_docker_binary().passed:
        missing.append("docker.io")
    if not check_compose_v2().passed:
        missing.append("docker-compose-plugin")
    # The host agent gets its own venv. Debian/Kali ship ensurepip separately, and
    # without it `python3 -m venv` produces an interpreter with no pip at all.
    if subprocess.run(["python3", "-m", "venv", "--help"],
                      capture_output=True).returncode != 0:
        missing.append("python3-venv")

    if not missing:
        console.print("  [dim]Docker, Compose v2 and python3-venv already present[/dim]")
        return

    console.print(f"  Missing: [yellow]{', '.join(missing)}[/yellow]")

    if shutil.which("apt-get") is None:
        console.print("  [yellow]No apt-get on this system — install those with your package "
                      "manager, then re-run.[/yellow]")
        return

    if not assume_yes and not typer.confirm(
        f"  Install {', '.join(missing)} with apt (sudo required)?", default=True
    ):
        console.print("  [dim]Skipped.[/dim]")
        return

    subprocess.run(["sudo", "apt-get", "update"])
    failed = []
    for package in missing:
        # One at a time: docker-compose-plugin is absent from some distro repos, and a
        # single apt-get call would abandon docker.io along with it.
        if subprocess.run(["sudo", "apt-get", "install", "-y", package]).returncode != 0:
            failed.append(package)

    if failed:
        console.print(f"  [red]Could not install: {', '.join(failed)}[/red]")
        console.print("  [dim]Docker's own repo carries these: "
                      "https://docs.docker.com/engine/install/[/dim]")
    else:
        console.print("  [green]System packages installed[/green]")


def _ensure_env_file() -> None:
    env_path = PROJECT_ROOT / ".env"
    example = PROJECT_ROOT / ".env.example"

    if env_path.exists():
        console.print("  [dim].env already exists — leaving your settings alone[/dim]")
    elif example.exists():
        shutil.copy(example, env_path)
        console.print("  [green]Created .env from .env.example[/green]")
    else:
        env_path.touch()
        console.print("  [yellow]No .env.example found — created an empty .env[/yellow]")

    # .env.example ships an empty token and a shared "change-me" signing key. Left as-is,
    # every Bars install would sign its JWTs with the same published secret.
    ensure_agent_token()
    ensure_env_secret("AUTH_SECRET_KEY", "JWT signing key (auto-generated).")


@app.command()
def install(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip all confirmation prompts"),
    skip_system: bool = typer.Option(False, "--skip-system", help="Don't apt-install missing system packages"),
    skip_build: bool = typer.Option(False, "--skip-build", help="Don't build Docker images (the slow step)"),
):
    """One-shot setup: system packages, Docker group access, .env secrets, agent venv, images.

    Safe to re-run — every step is a no-op once it is done.
    """
    console.print(Panel("[bold]Bars[/bold]", subtitle="installing..."))

    console.print("\n[bold][1/5] System packages[/bold]")
    if skip_system:
        console.print("  [dim]Skipped (--skip-system)[/dim]")
    else:
        _install_system_packages(yes)

    console.print("\n[bold][2/5] Docker access for this user[/bold]")
    docker_group = _ensure_docker_group(yes, indent="  ")

    console.print("\n[bold][3/5] Environment file[/bold]")
    _ensure_env_file()

    console.print("\n[bold][4/5] Host Agent dependencies[/bold]")
    try:
        ensure_agent_venv()
    except subprocess.CalledProcessError:
        console.print("  [red]Failed to build the agent venv. Is python3-venv installed?[/red]")
        raise typer.Exit(1)

    console.print("\n[bold][5/5] Docker images (backend + frontend dependencies)[/bold]")
    if skip_build:
        console.print("  [dim]Skipped (--skip-build)[/dim]")
    elif not check_docker_daemon().passed:
        # Expected right after the group was added — the new group needs a fresh login.
        console.print("  [yellow]Docker daemon not reachable from this session — skipping "
                      "the image build.[/yellow]")
        console.print("  [dim]Re-run './bars install' after logging back in, or './bars update'.[/dim]")
    else:
        rc = compose_build(dev=False, no_cache=False)
        if rc != 0:
            console.print("  [red]Image build failed.[/red]")
            raise typer.Exit(1)
        console.print("  [green]Images built[/green]")

    console.print("\n[bold]Final check[/bold]")
    passed, _ = run_all_checks(check_ports=False)

    if docker_group in ("added", "relogin"):
        console.print(Panel(
            f"{RELOGIN_HINT}\n\nThen finish with:\n"
            "  [bold]./bars install[/bold]   (builds the images this run had to skip)\n"
            "  [bold]./bars start[/bold]",
            title="[bold yellow]One more step[/bold yellow]",
        ))
    elif passed:
        console.print(Panel(
            "[green]Install complete.[/green]\n\n"
            "  [bold]./bars start[/bold]\n"
            "  [dim]then seed the library:[/dim]\n"
            "  [bold]docker compose exec backend python load_library_seed.py[/bold]",
            title="[bold green]Bars[/bold green]",
        ))
    else:
        console.print("\n[yellow]Install finished with failing checks above. "
                      "Fix them, then re-run './bars install'.[/yellow]")


@app.command(name="fix-docker")
def fix_docker(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
):
    """Let this (non-root) user talk to the Docker daemon by adding them to the 'docker' group."""
    if _ensure_docker_group(yes) == "added":
        console.print(f"\n{RELOGIN_HINT}")


@app.command()
def purge(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
    keep_images: bool = typer.Option(False, "--keep-images", help="Preserve Docker images"),
):
    """Delete all data: DB, storage, reports, venvs, Docker volumes/images."""
    items = [
        "SQLite database files (pentest_toolbox.db*)",
        "storage/ contents",
        "reports/ contents",
        ".bars/ (state, logs, CLI venv)",
        "host_agent/venv/",
        "Docker volumes for this project",
    ]
    if not keep_images:
        items.append("Docker images built by this project")

    if not yes:
        console.print("[bold red]This will permanently delete:[/bold red]")
        for item in items:
            console.print(f"  - {item}")
        console.print()
        confirmed = typer.confirm("Are you sure?", default=False)
        if not confirmed:
            console.print("[dim]Aborted.[/dim]")
            raise typer.Exit(0)

    state = load_state()
    if state:
        console.print("[bold]Stopping services first...[/bold]")
        stop_agent(state.agent_pid)
        compose_down(dev=state.dev_mode)
        clear_state()

    purge_docker(keep_images=keep_images)
    purge_files()

    console.print("\n[bold green]Purge complete.[/bold green]")


if __name__ == "__main__":
    app(prog_name="bars")
