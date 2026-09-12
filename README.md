# Bars

**Collaborative orchestration for internal / Active Directory penetration tests.**

Bars turns an engagement from a pile of ad-hoc terminal commands into a shared, repeatable
workflow: a versioned **library of checklists and flows** drives tool execution, output is parsed
into **structured assets** (hosts, services, shares), alert matches become persistent **findings**,
and findings render into a client-ready **report** — with multiple operators collaborating live over
multi-node sync.

> ⚠️ **For authorized security testing only.** Use only against systems you have written permission
> to test. See [Security model](#security-model) before putting this on any shared network.

---

## Contents

- [Why](#why) · [Concepts](#concepts) · [Architecture](#architecture)
- [Quick start](#quick-start) · [Configuration](#configuration) · [CLI reference](#cli-reference)
- [The methodology library](#the-methodology-library) · [Findings & reporting](#findings--reporting) · [Multi-node collaboration](#multi-node-collaboration)
- [Development](#development) · [Troubleshooting](#troubleshooting) · [Security model](#security-model)

---

## Why

Most offensive tooling is powerful but stateless: you run `nmap`, `netexec`, `certipy`… and the
results live in your scrollback and your memory. Bars closes the loop of a real engagement
end to end:

```
Scope → Discover → Enumerate → Loot → Findings → Report
```

- **Codify methodology as data.** Tools aren't hard-coded — every check is a *checklist item*
  (a command template + how to parse it + what a finding looks like). Add a tool by adding data,
  not code.
- **Keep the state.** Hosts, services, shares, executions, variables, findings and evidence are
  first-class records, not scrollback.
- **Automate the chain.** Visual *flows* run multi-step pipelines (staged/parallel execution,
  conditions, failure policies) and pass outputs between steps.
- **Collaborate.** Two or more operators on separate machines replicate their work in real time.
- **Deliver.** Findings + evidence become a severity-ranked report (Markdown / HTML / PDF).

---

## Concepts

| Concept | What it is |
|---|---|
| **Project** | An engagement. Holds scope, hosts, checklists, flows, variables, findings, reports. |
| **Host / Service** | Discovered assets, populated automatically from parsed tool output. |
| **Checklist item** | A single check: `command_template`, `output_regex`, `alert_patterns`, `finding_template`. The unit of "run a tool". |
| **Flow** | A visual DAG of checklist items — a runnable pipeline with staged/parallel execution, conditions and failure policies. |
| **Execution** | One run of an item (single, or batched across many targets), streamed live from the host agent. |
| **Finding** | A persistent vulnerability/observation with severity, status, CVSS/CWE/CVE, remediation and evidence — auto-created when an item's alert pattern matches. |
| **Evidence** | Screenshots, files or captured output attached to a finding as proof for the report. |
| **Variable** | Scoped key/values (`{username}`, `{domain}`, `{pdcip}`…) substituted into commands; chainable from one step's output to the next. |
| **AD Domain** | First-class Active Directory context (DC, trusts) for multi-domain engagements. |
| **Library** | The global, importable set of checklist/flow/variable templates — the shipped methodology. |
| **Sync peer** | Another Bars node you replicate with for live collaboration. |

### The data-driven tool model

There is **no hard-coded list of tools**. A check is described entirely as data:

```jsonc
{
  "name": "NXC SMB ms17-010",
  "command_template": "netexec smb {targets} -u {username} -p {password} -M ms17-010",
  "output_regex":     { "vuln": "VULNERABLE" },        // scrape values out of stdout
  "alert_patterns":   { "critical": ["VULNERABLE"] },   // what triggers an alert
  "finding_template": {                                  // what the finding looks like
    "title": "MS17-010 (EternalBlue) — Unauthenticated SMB RCE",
    "severity": "critical", "cve": "CVE-2017-0144",
    "remediation": "Apply MS17-010; disable SMBv1; segment legacy hosts."
  }
}
```

Adding `nuclei`, `ffuf`, `kerbrute`, `certipy`, … is a matter of adding items like this to the
library — no framework code changes.

---

## Architecture

```
┌────────────┐    HTTP / WebSocket    ┌──────────────────────┐   SSE    ┌──────────────┐
│  Frontend  │ ◀────────────────────▶ │   Backend (FastAPI)  │ ◀──────▶ │  Host Agent  │
│ React/Vite │        :3000           │  API · Core · SQLite │  :8001   │ runs tools   │
└────────────┘                        │        :8000         │          │ on the host  │
        ▲                             └──────────┬───────────┘          └──────────────┘
        │ bars (CLI)                             │ /sync push+pull
        │                             ┌──────────▼───────────┐
        └─────────────────────────────│      Peer Bars       │  (multi-node collaboration)
                                      └──────────────────────┘
```

The backend and frontend run in Docker. The **host agent runs natively on the operator's machine**,
outside Docker, so tools get real network access (VPN routes, raw sockets, kernel capabilities).

### Backend core (`backend/app/core/`)

| Module | Responsibility |
|---|---|
| `orchestrator.py` | Heart of execution. Renders commands, runs them (single or **batch** across many targets), streams output, parses results, discovers assets, injects variables, and **creates findings** from alert matches. |
| `flow_manager.py` | The flow engine. Advances a flow stage-by-stage on execution-completion events, evaluates step **conditions** (safe typed comparator), honours per-step **failure policies**, and chains outputs between steps. |
| `parser.py` · `service_parser.py` · `share_parser.py` | Turn raw output into structure: generic regex extraction, plus dedicated parsers for nmap/rustscan/masscan/netexec (→ hosts & services) and smbmap/netexec/showmount (→ shares & files). |
| `findings.py` | Bridges the ephemeral alert pipeline to durable, **deduped `Finding` rows** using each item's `finding_template`. |
| `templating.py` | `{variable}` / `{step.output}` substitution engine for command templates. |
| `host_runner.py` | Client for the host agent (stream execution, cancel, sync exec). |
| `notifications.py` | Pattern-based alerts + WebSocket broadcasting of live events. |
| `sync.py` · `sync_worker.py` | Multi-node replication — append-only event log with logical clocks, deterministic FK-ordered ingestion, background pull/push worker. |
| `collaboration.py` | Checklist **claims** (optimistic locking) so operators don't duplicate work. |
| `script_scanner.py` | Generates per-service follow-up scan items from discovered services. |

### API

REST routers under `/api/v1`: `auth`, `users`, `projects`, `hosts`, `checklists`, `executions`,
`flows`, `files`, `search`, `reports`, `variables`, `sync`, `library`, `ad-domains`, `findings`,
`evidence`. Interactive docs at **http://localhost:8000/docs**.

WebSockets: `/ws` (global), `/ws/project/{id}`, `/ws/execution/{id}`.

### Components

- **`frontend/`** — React 18 + Vite + Tailwind + Zustand + React Flow. Dashboard, project detail,
  host view, visual flow editor, Findings board, reports, library.
- **`host_agent/`** — small FastAPI process running *outside* Docker. Streams stdout/stderr over SSE
  with strict timeouts and process-group kill (SIGTERM→SIGKILL).
- **`cli/` (`bars`)** — start/stop the stack + host agent, health checks, logs, prerequisite fixes.

---

## Quick start

### Prerequisites

| Requirement | Notes |
|---|---|
| Docker Engine + Compose v2 | `docker compose version` must work |
| Your user in the `docker` group | handled by `./bars install` (or `./bars fix-docker`) — see [security note](#security-model) |
| Python 3.11+ on the host | for the host agent (runs natively, not in Docker) |
| Your pentest tools on the host | netexec, nmap, impacket, certipy, responder, … — the agent shells out to whatever is in `PATH` |

### Install

```bash
git clone <repo-url> bars && cd bars

# 1. One-shot setup: Docker packages, docker-group access, .env with generated
#    secrets, host agent venv, Docker images. Safe to re-run.
./bars install

# 2. Bring up backend (:8000), frontend (:3000) and the host agent (:8001)
./bars start

# 3. Seed the methodology library (checklists, flows, variables)
docker compose exec backend python load_library_seed.py
```

If `install` had to add you to the `docker` group, log out and back in (or `newgrp docker`)
and run `./bars install` again — the image build is skipped until the group applies.

`install` never edits settings you already have. It only fills in what is missing: it
creates `.env` from `.env.example` when absent, and generates `HOST_AGENT_TOKEN` and
`AUTH_SECRET_KEY` rather than leaving the shipped placeholders in place. Set a unique
`NODE_ID` in `.env` yourself if you plan to [sync with other nodes](#multi-node-collaboration).

Open **http://localhost:3000**. The default account is **`admin` / `admin`** — you are forced to
change the password on first login.

<details>
<summary>Without the CLI (raw Docker Compose)</summary>

```bash
cp .env.example .env
docker compose up --build -d
docker compose exec backend python load_library_seed.py

# Host agent, natively on your machine. HOST_AGENT_TOKEN must match the backend's .env —
# generate one with: python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
cd host_agent && python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
HOST_AGENT_TOKEN=<same-as-.env> ./venv/bin/python main.py        # listens on :8001
```
</details>

### First engagement

1. **Create a project** and enter scope (targets, exclusions).
2. **Import library groups** into the project — pick the methodology you need (Recon, Unauth, …).
3. **Set variables** (`{username}`, `{password}`, `{domain}`, `{pdcip}`) at project or AD-domain scope.
4. **Run items or a flow.** Output streams live; hosts, services and shares populate automatically.
5. **Work the Findings board** — alert matches land there with severity and remediation prefilled.
6. **Attach evidence**, then **generate a report** (Markdown / HTML / PDF).

---

## Configuration

All settings live in `.env` (see `.env.example`). Defaults are in `backend/app/config.py`.

| Key | Default | Meaning |
|---|---|---|
| `DEBUG` | `false` | Verbose errors + reload. Keep off on anything shared. |
| `DATABASE_URL` | `sqlite+aiosqlite:///./pentest_toolbox.db` | SQLite in WAL mode. |
| `STORAGE_PATH` / `REPORTS_PATH` | `/app/storage`, `/app/reports` | Container paths; bind-mounted to `./storage` and `./reports`. |
| `AUTH_REQUIRED` | `true` | Require login for the API/UI. |
| `AUTH_SECRET_KEY` | `ptf-dev-change-me` | Signs session tokens. `bars install` generates a real one; **change it yourself if you skipped install.** |
| `AUTH_TOKEN_TTL_MINUTES` | `480` | Session lifetime. |
| `WS_REQUIRE_AUTH` | `true` | Require a token on the WebSocket. |
| `HOST_AGENT_URL` | `http://host.docker.internal:8001` | Where the backend reaches the host agent. |
| `HOST_AGENT_TOKEN` | *(empty)* | Shared secret for the host agent. Auto-generated by `bars install` / `bars start`; the agent won't run without it. |
| `MAX_CONCURRENT_TASKS` | `10` | Global execution concurrency. |
| `BATCH_CONCURRENCY` | `5` | Parallel children per batched item (e.g. 900 targets, 5 at a time). |
| `TASK_TIMEOUT` | `3600` | Per-task timeout, seconds. |
| `SYNC_ENABLED` | `false` | Enable multi-node replication. |
| `NODE_ID` | `node-<hostname>` | **Must be unique per node** — it is the last-write-wins tiebreak. |
| `SYNC_PEERS` | *(empty)* | Comma-separated peer backend URLs. |
| `PUBLIC_BASE_URL` | *(empty)* | URL shown in the UI for collaborators to add you as a peer. |
| `PROJECTS_BASE_PATH` | `$HOME/Pentests` | Where per-project working directories are created on the host. |
| `CLAIMS_ENABLED` / `DEFAULT_CLAIM_LEASE_SECONDS` | `true` / `900` | Checklist claim locking. |

---

## CLI reference

`./bars` bootstraps its own venv on first run. `bars install` sets up everything *around* it —
system packages, Docker access, secrets, images.

| Command | What it does |
|---|---|
| `bars install [-y] [--skip-system] [--skip-build]` | One-shot host setup: apt-install missing Docker/Compose, add you to the `docker` group, create `.env` and generate its secrets, build the agent venv and the Docker images. Idempotent. |
| `bars start [--dev]` | Prerequisite checks, start host agent, `docker compose up`. `--dev` runs foreground with hot-reload and streamed logs. |
| `bars stop` | Stop the agent and tear down Compose. |
| `bars restart [--dev]` | Stop, then start. |
| `bars status` | Table of backend / frontend / agent state, ports, PIDs. |
| `bars health` | HTTP probe of all three endpoints with response times. |
| `bars logs [backend\|frontend\|agent] [-F]` | Tail logs. `-F` disables follow. |
| `bars update [--no-cache]` | Rebuild Docker images, offer to restart. |
| `bars fix-docker [-y]` | Add the current user to the `docker` group (confirms first — see [Security model](#security-model)). |
| `bars purge [-y] [--keep-images]` | **Destructive.** Delete DB, storage, reports, venvs, Docker volumes/images. |

---

## The methodology library

Ships an **internal / Active Directory** methodology (netexec-heavy):

- **Recon** — host & service discovery (nmap, rustscan, netexec sweeps), relay-list generation.
- **Unauthenticated** — null/guest access across SMB/RDP/WinRM/MSSQL/SSH/FTP/WMI/NFS; share
  discovery; SNMP; DNS (zone transfer, SRV records).
- **Authenticated enum** — netexec SMB/LDAP modules (RID brute, ADCS, ASREPRoast, Kerberoast,
  delegation, LAPS, coercion checks, MS17-010 / SMBGhost / PrintNightmare…).
- **Credential access** — BloodHound collection, kerbrute, password-policy check, spraying,
  AS-REP/Kerberoast (impacket), GPP passwords, SAM/LSA/**NTDS (DCSync)** dumping, Certipy.
- **Coercion & relay** — Responder, `ntlmrelayx` (→ SMB / ADCS ESC8), mitm6, Coercer.
- **Lateral movement** — nxc command exec, wmiexec/psexec/atexec, MSSQL `xp_cmdshell`.

Finding-worthy checks carry a `finding_template`, so a matching scan **auto-populates the Findings
board** with the right title, severity and remediation. An **AD Internal Kill-Chain** flow stages
these into a single runnable pipeline (recon → unauth → BloodHound → roasting → spray → secrets).

The library lives in `backend/library_seed.json`. Re-seeding **replaces** all global templates
(project-scoped copies are untouched):

```bash
docker compose exec backend python load_library_seed.py
```

---

## Findings & reporting

- Alert matches persist as **Findings** (severity, status, CVSS/CWE/CVE, remediation, occurrences),
  editable on a severity board and attributable to hosts. Duplicates are merged, not re-created.
- Attach **evidence** (screenshots, files, output excerpts) to any finding.
- Generate a report in **Markdown**, **HTML** or **PDF** with an executive summary from the severity
  rollup, scope (incl. exclusions), findings-by-severity with remediation and embedded evidence, and
  an asset inventory. Reports land in `./reports/`.

PDF rendering uses WeasyPrint and its native pango/harfbuzz libraries, which are installed in the
backend image. If they're ever missing the API returns **503** with a clear message rather than
failing the whole report — fall back to `format=markdown`.

---

## Multi-node collaboration

Each operator runs their own full stack on their own machine. Enable sync, give each node a unique
`NODE_ID`, and add each other as peers in the UI (**Sync Peers**):

```env
SYNC_ENABLED=true
NODE_ID=node-alice          # must differ per node
PUBLIC_BASE_URL=http://192.168.1.10:8000
```

Projects, hosts, executions, findings and claims then replicate both ways. A background worker
pulls and pushes an append-only event log; ingestion is FK-ordered and idempotent, so nodes
converge regardless of arrival order. **Checklist claims** stop two operators running the same item.

To test sync on a single machine, `docker-compose.sync.yml` brings up two independent nodes
(op1 on :3000/:8000, op2 on :3001/:8001). See `COLLABORATION.md` for the full walkthrough.

---

## Development

```bash
./bars start --dev              # hot-reload backend + frontend, logs in foreground

cd backend && python -m pytest tests/ -q     # 13 tests
cd frontend && npm ci && npm run build       # production bundle
```

**Database schema.** `init_db()` runs `create_all` plus an idempotent additive migration pass on
every boot (`backend/app/database.py`), so a running instance self-heals across upgrades. Alembic
revisions in `backend/alembic/versions/` are the canonical record of those changes.

**Adding a tool.** Don't write code — add a checklist item to `backend/library_seed.json` with a
`command_template`, `output_regex`, `alert_patterns` and (if it can produce a finding) a
`finding_template`, then re-seed.

**Adding a parser.** Output shapes that regex can't handle get a dedicated function in
`service_parser.py` / `share_parser.py` and a case in `tests/test_parsers.py`.

### Project layout

```
backend/     FastAPI app (api/ · core/ · models/ · schemas/ · websocket/) + library seed + alembic
frontend/    React SPA (pages/ · components/ · stores/ · services/)
host_agent/  standalone tool-execution agent (runs natively on the host)
cli/         bars — stack & agent management
scripts/     rsync helpers for pushing to a second machine
```

---

## Troubleshooting

| Symptom | Fix |
|---|---|
| `permission denied … /var/run/docker.sock` | `./bars fix-docker`, then log out/in or `newgrp docker`. |
| Executions stay PENDING / "host agent unreachable" | Agent not running: `./bars status`. On Linux the backend reaches it via `host.docker.internal`, which needs the `extra_hosts: host-gateway` mapping already in the compose files. |
| Tool "not found" in an execution | The agent runs on the *host*, so the binary must be in the host user's `PATH` — not inside a container. |
| Port 3000/8000/8001 already in use | `./bars start` fails the prereq check and names the port; free it or change the mapping in `docker-compose.yml`. |
| Frontend builds with a stale dependency | `rm -rf frontend/node_modules && npm ci` — `package-lock.json` is authoritative. |
| Executions marked CANCELLED after a restart | Expected: startup cleans up orphaned PENDING/RUNNING rows from a previous crash. |
| Want a clean slate | `./bars purge` — deletes DB, storage, reports, volumes. Irreversible. |

---

## Security model

Read this before running Bars anywhere but your own workstation.

- **The host agent executes arbitrary commands as your user.** Every endpoint requires a shared
  secret (`HOST_AGENT_TOKEN`, sent as `X-Bars-Token`); the agent refuses to start without one and
  `bars start` generates it into `.env` on first run. It still binds `0.0.0.0:8001` because the
  backend container reaches it through `host.docker.internal`, which resolves to the Docker bridge
  gateway rather than loopback — so **also firewall port 8001** to your docker bridge and VPN
  interfaces. The token is what keeps the rest of the LAN out; defence in depth is what keeps a
  leaked token from mattering.
- **`bars fix-docker` grants root-equivalent access.** Docker group members can start privileged
  containers and mount the host filesystem. The command warns and confirms before doing it. Only
  run it on a machine you control.
- **Set `AUTH_SECRET_KEY`.** The default (`ptf-dev-change-me`) makes session tokens forgeable.
  Set `AUTH_REQUIRED=true` and `WS_REQUIRE_AUTH=true` for any multi-operator setup.
- **Change the default `admin` / `admin` password.** The first login forces it; don't leave the
  account seeded and unused.
- **Sync peers are trusted.** A peer can write records into your database. Only pair with nodes
  run by your own team, over a trusted network.
- **The database is engagement data.** `pentest_toolbox.db`, `storage/` and `reports/` hold
  credentials, hashes and client-identifying material. They are gitignored — keep it that way, and
  treat backups as classified as the engagement itself.
- CORS is currently `allow_origins=["*"]` for local development. Restrict it before exposing the
  backend beyond localhost.

---

## License

Copyright (C) 2026 saidtunc.

Bars is free software under the **GNU Affero General Public License v3.0** — see [LICENSE](LICENSE).
You may use, modify and redistribute it, but if you distribute a modified version, *or run one as a
network service that others interact with*, you must make your source available to those users
under the same license.

Commercial licensing without the AGPL obligations is available — open an issue to ask.

---

### The name

**Bars** (*bars*) is the Old Turkic word for leopard. It names the third year of the twelve-animal
Turkic calendar — *Bars yılı* — and it was borne by **Bars Bäg**, the Yenisei Kyrgyz khagan recorded
in the Orkhon inscriptions. The Anatolian leopard it refers to is officially presumed extinct and
still occasionally sighted: patient, solitary, rarely seen, and decisive when it moves. A reasonable
thing to name an offensive-security tool after.
