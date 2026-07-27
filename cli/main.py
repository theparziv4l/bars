from __future__ import annotations

import getpass
import os
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

from cli.agent import ensure_agent_venv, start_agent_background, stop_agent, agent_status
from cli.checks import check_docker_daemon, check_docker_group, run_all_checks
from cli.config import (
    AGENT_PORT,
    BACKEND_PORT,
    FRONTEND_PORT,
    HOST_AGENT_LOG,
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


@app.command(name="fix-docker")
def fix_docker(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
):
    """Let this (non-root) user talk to the Docker daemon by adding them to the 'docker' group."""
    user = os.environ.get("USER") or getpass.getuser()

    if check_docker_group().passed:
        if check_docker_daemon().passed:
            console.print(f"[green]User '{user}' already has Docker access. Nothing to do.[/green]")
        else:
            console.print(
                f"[yellow]User '{user}' is in the 'docker' group, but the daemon is unreachable "
                f"from this session.[/yellow]\n"
                "Log out and back in (or run [bold]newgrp docker[/bold]) to pick up the group.\n"
                "If it still fails, the daemon may be down: [bold]sudo systemctl start docker[/bold]."
            )
        raise typer.Exit(0)

    if not yes:
        console.print(
            "[bold red]Security warning:[/bold red] members of the 'docker' group can start "
            "privileged containers and mount the host filesystem. This is equivalent to giving "
            f"'{user}' passwordless root on this machine. Only do this on a machine you control.\n"
        )
        if not typer.confirm(f"Add user '{user}' to the 'docker' group?", default=False):
            console.print("[dim]Aborted.[/dim]")
            raise typer.Exit(0)

    console.print(f"[bold]Adding '{user}' to the 'docker' group (sudo required)...[/bold]")
    rc = subprocess.run(["sudo", "groupadd", "-f", "docker"]).returncode
    if rc == 0:
        rc = subprocess.run(["sudo", "usermod", "-aG", "docker", user]).returncode
    if rc != 0:
        console.print(f"[red]Failed. Run manually: sudo usermod -aG docker {user}[/red]")
        raise typer.Exit(1)

    console.print(
        f"\n[green]User '{user}' added to the 'docker' group.[/green]\n"
        "[yellow]Group membership only applies to new logins.[/yellow] Log out and back in "
        "(or run [bold]newgrp docker[/bold] in this shell), then verify with [bold]docker info[/bold]."
    )


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
