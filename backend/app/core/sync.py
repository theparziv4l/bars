"""Synchronization service for multi-node event replication."""
from __future__ import annotations

import logging

import asyncio
import shlex
from dataclasses import dataclass
from datetime import datetime, timedelta
import uuid
from typing import Any, Dict, Iterable, Optional

from sqlalchemy import delete, or_, select, tuple_
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models.checklist_claim import ChecklistClaim
from app.models.checklist import ChecklistGroup, ChecklistItem
from app.models.file import DiscoveredFile
from app.models.flow import Flow, FlowStep
from app.models.host import Host
from app.models.project import Project, ProjectStatus
from app.models.user import ProjectMember, User
from app.models.execution import Execution, ExecutionStatus
from app.models.variable import ProjectVariable
from app.models.sync import SyncCursor, SyncEvent, SyncPeer

# Service is in host module
from app.models.host import Service
from app.core.utils import utcnow

logger = logging.getLogger(__name__)

# Generic service names: do not overwrite a specific name with these when applying sync payloads
_SYNC_GENERIC_SERVICE_NAMES = frozenset({
    "unknown", "tcpwrapped", "tcpwrapped|ssl", "ssl|tcpwrapped",
    "tcpwrapped|http", "http|tcpwrapped", "tcpwrapped|https", "https|tcpwrapped",
})


def _sync_should_apply_service_name(existing_name: Optional[str], incoming_name: Optional[str]) -> bool:
    """Return True if we should overwrite existing service name with incoming (prefer specific over generic)."""
    if not incoming_name or not str(incoming_name).strip():
        return False
    new_lower = str(incoming_name).strip().lower()
    if new_lower == "unknown" or new_lower in _SYNC_GENERIC_SERVICE_NAMES:
        return False
    if not existing_name or not str(existing_name).strip():
        return True
    existing_lower = str(existing_name).strip().lower()
    if existing_lower in _SYNC_GENERIC_SERVICE_NAMES or existing_lower == "unknown":
        return True
    return new_lower != existing_lower


def _now_ts() -> int:
    return int(utcnow().timestamp() * 1_000_000)


def _event_key(logical_ts: int, source_node_id: str, event_id: str = "") -> tuple[int, str, str]:
    return logical_ts, source_node_id, event_id


def _apply_present(obj, payload: Dict[str, Any], fields: tuple[str, ...]) -> None:
    """Copy *fields* from *payload* onto *obj*, skipping keys the peer did not send.

    ``payload.get(x) or {}`` cannot tell "peer cleared it" from "peer never sent it"
    (older schema, partial event), and the second case silently destroys local data —
    e.g. host tags, which target resolution depends on. Presence is the signal.
    An explicit null is normalised to the empty container the column already holds,
    since these JSON columns are ``nullable=False``.
    """
    for name in fields:
        if name not in payload:
            continue
        value = payload[name]
        if value is None:
            current = getattr(obj, name, None)
            if isinstance(current, dict):
                value = {}
            elif isinstance(current, list):
                value = []
        setattr(obj, name, value)


class _FKNotReady(Exception):
    """Raised by an applier when a referenced parent row isn't present yet (cross-node
    clock skew). The ingest loop defers such events for retry instead of dropping them."""


# Max passes over a batch for deferred events. FK chains here are at most ~5 deep
# (file -> execution -> item -> group -> project); one extra pass proves quiescence.
_MAX_INGEST_PASSES = 6

# Whole-batch ingest attempts on SQLITE_BUSY. Concurrent ingests can hit
# BUSY_SNAPSHOT when upgrading a DEFERRED txn to a write (busy_timeout does
# not cover snapshot conflicts); rollback + redo from a fresh snapshot.
_MAX_INGEST_ATTEMPTS = 3


# Apply order for FK dependency: users first, then projects, then project_members, etc.
_ENTITY_APPLY_ORDER = (
    "users",
    "projects",
    "project_members",
    "checklist_groups",
    "checklist_items",
    "flows",
    "flow_steps",
    "hosts",
    "services",
    "project_variables",
    "executions",
    "discovered_files",
    "checklist_claims",
    "findings",
)
_ENTITY_ORDER_INDEX = {t: i for i, t in enumerate(_ENTITY_APPLY_ORDER)}


def _ingest_sort_key(event: Dict[str, Any]) -> tuple[int, str, int]:
    """Sort by logical_ts, source_node_id, then by entity dependency order."""
    try:
        logical_ts = int(event.get("logical_ts", 0))
    except (TypeError, ValueError):
        logical_ts = 0  # malformed envelope: sorts first, skipped in _ingest_one
    source_node_id = str(event.get("source_node_id", ""))
    entity_type = str(event.get("entity_type", ""))
    type_order = _ENTITY_ORDER_INDEX.get(entity_type, 999)
    return (logical_ts, source_node_id, type_order)


async def get_peer_urls_from_db(db: AsyncSession) -> list[str]:
    """Return list of peer base URLs from SyncPeer table (in-app configured peers)."""
    result = await db.execute(select(SyncPeer.base_url))
    return [row[0] for row in result.fetchall()]


async def execution_sync_payload(db: AsyncSession, execution: Execution) -> Dict[str, Any]:
    """Build full sync payload for an execution (create/update)."""
    item_public_id = None
    if execution.item_id:
        item_result = await db.execute(
            select(ChecklistItem).where(ChecklistItem.id == execution.item_id)
        )
        item = item_result.scalar_one_or_none()
        item_public_id = item.public_id if item else None
    host_public_id = None
    if execution.host_id:
        host_result = await db.execute(select(Host).where(Host.id == execution.host_id))
        host = host_result.scalar_one_or_none()
        host_public_id = host.public_id if host else None
    return {
        "public_id": execution.public_id,
        "item_id": execution.item_id,
        "item_public_id": item_public_id,
        "host_id": execution.host_id,
        "host_public_id": host_public_id,
        "status": execution.status.value if execution.status else "pending",
        "command": execution.command or "",
        "variables_used": execution.variables_used or {},
        "version": getattr(execution, "version", 1) or 1,
        "stdout": getattr(execution, "stdout", None),
        "stderr": getattr(execution, "stderr", None),
        "exit_code": getattr(execution, "exit_code", None),
        "started_at": execution.started_at.isoformat() if getattr(execution, "started_at", None) else None,
        "completed_at": execution.completed_at.isoformat() if getattr(execution, "completed_at", None) else None,
        "started_by_user_id": getattr(execution, "started_by_user_id", None),
        "updated_at": execution.updated_at.isoformat() if execution.updated_at else None,
        "deleted_at": execution.deleted_at.isoformat() if getattr(execution, "deleted_at", None) else None,
    }


async def host_sync_payload(db: AsyncSession, host: Host) -> Dict[str, Any]:
    """Build sync payload for a host (for use by API and orchestrator)."""
    project_public_id = None
    if host.project_id:
        proj_result = await db.execute(select(Project).where(Project.id == host.project_id))
        project = proj_result.scalar_one_or_none()
        if project:
            project_public_id = project.public_id
    return {
        "public_id": host.public_id,
        "project_id": host.project_id,
        "project_public_id": project_public_id,
        "ip_address": host.ip_address,
        "hostname": host.hostname,
        "fqdn": host.fqdn,
        "os_info": host.os_info,
        "status": host.status,
        "notes": host.notes,
        "extra_data": host.extra_data or {},
        "tags": host.tags or [],
        "updated_at": host.updated_at.isoformat() if host.updated_at else None,
        "deleted_at": host.deleted_at.isoformat() if host.deleted_at else None,
    }


async def finding_sync_payload(db: AsyncSession, finding) -> Dict[str, Any]:
    """Build sync payload for a finding (create/update)."""
    project_public_id = None
    if finding.project_id:
        proj = (await db.execute(select(Project).where(Project.id == finding.project_id))).scalar_one_or_none()
        project_public_id = proj.public_id if proj else None
    host_public_id = None
    if finding.host_id:
        host = (await db.execute(select(Host).where(Host.id == finding.host_id))).scalar_one_or_none()
        host_public_id = host.public_id if host else None
    item_public_id = None
    if finding.item_id:
        item = (await db.execute(
            select(ChecklistItem).where(ChecklistItem.id == finding.item_id)
        )).scalar_one_or_none()
        item_public_id = item.public_id if item else None
    execution_public_id = None
    if finding.execution_id:
        ex = (await db.execute(
            select(Execution).where(Execution.id == finding.execution_id)
        )).scalar_one_or_none()
        execution_public_id = ex.public_id if ex else None
    return {
        "public_id": finding.public_id,
        "project_public_id": project_public_id,
        "host_public_id": host_public_id,
        "item_public_id": item_public_id,
        "execution_public_id": execution_public_id,
        "title": finding.title,
        "description": finding.description,
        "severity": finding.severity.value if finding.severity else "info",
        "status": finding.status.value if finding.status else "open",
        "cvss_vector": finding.cvss_vector,
        "cvss_score": finding.cvss_score,
        "cwe": finding.cwe,
        "cve": finding.cve,
        "remediation": finding.remediation,
        "notes": finding.notes,
        "references": finding.references or [],
        "source": finding.source,
        "evidence_text": finding.evidence_text,
        "occurrences": finding.occurrences,
        "discovered_at": finding.discovered_at.isoformat() if finding.discovered_at else None,
        "updated_at": finding.updated_at.isoformat() if finding.updated_at else None,
        "deleted_at": finding.deleted_at.isoformat() if finding.deleted_at else None,
    }


async def service_sync_payload(db: AsyncSession, service: Service) -> Dict[str, Any]:
    """Build sync payload for a service."""
    host_public_id = None
    if service.host_id:
        host_result = await db.execute(select(Host).where(Host.id == service.host_id))
        host = host_result.scalar_one_or_none()
        if host:
            host_public_id = host.public_id
    return {
        "public_id": service.public_id,
        "host_id": service.host_id,
        "host_public_id": host_public_id,
        "port": service.port,
        "protocol": service.protocol or "tcp",
        "name": service.name,
        "version": service.version,
        "product": service.product,
        "state": service.state,
        "banner": service.banner,
        "ssl": getattr(service, "ssl", False),
        "extra_data": service.extra_data or {},
        "discovered_at": service.discovered_at.isoformat() if service.discovered_at else None,
    }


async def discovered_file_sync_payload(db: AsyncSession, discovered_file: DiscoveredFile) -> Dict[str, Any]:
    """Build sync payload for a discovered file/share."""
    host_public_id = None
    execution_public_id = None
    if discovered_file.host_id:
        host_result = await db.execute(select(Host).where(Host.id == discovered_file.host_id))
        h = host_result.scalar_one_or_none()
        if h:
            host_public_id = h.public_id
    if discovered_file.execution_id:
        exec_result = await db.execute(
            select(Execution).where(Execution.id == discovered_file.execution_id)
        )
        ex = exec_result.scalar_one_or_none()
        if ex:
            execution_public_id = ex.public_id
    return {
        "public_id": discovered_file.public_id,
        "host_id": discovered_file.host_id,
        "host_public_id": host_public_id,
        "execution_id": discovered_file.execution_id,
        "execution_public_id": execution_public_id,
        "share_name": discovered_file.share_name,
        "path": discovered_file.path,
        "name": discovered_file.name,
        "file_type": discovered_file.file_type,
        "size": discovered_file.size,
        "permissions": discovered_file.permissions or {},
        "is_readable": discovered_file.is_readable,
        "is_writable": discovered_file.is_writable,
        "is_executable": getattr(discovered_file, "is_executable", False),
        "is_interesting": discovered_file.is_interesting,
        "interest_reason": discovered_file.interest_reason,
        "content_preview": discovered_file.content_preview,
        "extra_data": discovered_file.extra_data or {},
        "discovered_at": discovered_file.discovered_at.isoformat() if discovered_file.discovered_at else None,
    }


async def flow_sync_payload(db: AsyncSession, flow: Flow) -> Dict[str, Any]:
    """Build sync payload for a flow."""
    project_public_id = None
    if flow.project_id:
        proj_result = await db.execute(select(Project).where(Project.id == flow.project_id))
        project = proj_result.scalar_one_or_none()
        if project:
            project_public_id = project.public_id
    return {
        "public_id": flow.public_id,
        "project_id": flow.project_id,
        "project_public_id": project_public_id,
        "name": flow.name,
        "description": flow.description,
        "is_template": flow.is_template,
        "flow_definition": flow.flow_definition or {},
        "tags": flow.tags or [],
        "created_at": flow.created_at.isoformat() if flow.created_at else None,
        "updated_at": flow.updated_at.isoformat() if flow.updated_at else None,
    }


async def project_variable_sync_payload(
    db: AsyncSession, variable: ProjectVariable
) -> Dict[str, Any]:
    """Build sync payload for a project variable (for API and orchestrator)."""
    project_public_id = None
    host_public_id = None
    if variable.project_id:
        proj_result = await db.execute(
            select(Project).where(Project.id == variable.project_id)
        )
        project = proj_result.scalar_one_or_none()
        if project:
            project_public_id = project.public_id
    if variable.host_id:
        host_result = await db.execute(
            select(Host).where(Host.id == variable.host_id)
        )
        host = host_result.scalar_one_or_none()
        if host:
            host_public_id = host.public_id
    return {
        "public_id": variable.public_id,
        "project_id": variable.project_id,
        "project_public_id": project_public_id,
        "host_id": variable.host_id,
        "host_public_id": host_public_id,
        "key": variable.key,
        "value": variable.value,
        "var_type": variable.var_type or "string",
        "updated_at": variable.updated_at.isoformat() if variable.updated_at else None,
        "deleted_at": variable.deleted_at.isoformat() if variable.deleted_at else None,
    }


async def claim_sync_payload(db: AsyncSession, claim: ChecklistClaim) -> Dict[str, Any]:
    """Build sync payload for a checklist claim."""
    project_public_id = None
    item_public_id = None
    host_public_id = None
    user_public_id = None
    if claim.project_id:
        proj_result = await db.execute(
            select(Project).where(Project.id == claim.project_id)
        )
        project = proj_result.scalar_one_or_none()
        if project:
            project_public_id = project.public_id
    if claim.item_id:
        item_result = await db.execute(
            select(ChecklistItem).where(ChecklistItem.id == claim.item_id)
        )
        item = item_result.scalar_one_or_none()
        if item:
            item_public_id = item.public_id
    if claim.host_id is not None:
        host_result = await db.execute(
            select(Host).where(Host.id == claim.host_id)
        )
        host = host_result.scalar_one_or_none()
        if host:
            host_public_id = host.public_id
    if claim.claimed_by_user_id is not None:
        user_result = await db.execute(
            select(User).where(User.id == claim.claimed_by_user_id)
        )
        c_user = user_result.scalar_one_or_none()
        if c_user:
            user_public_id = c_user.public_id
    return {
        "public_id": claim.public_id,
        "project_id": claim.project_id,
        "project_public_id": project_public_id,
        "item_id": claim.item_id,
        "item_public_id": item_public_id,
        "host_id": claim.host_id,
        "host_public_id": host_public_id,
        "host_scope_key": claim.host_scope_key,
        "claimed_by_user_id": claim.claimed_by_user_id,
        "claimed_by_user_public_id": user_public_id,
        "claimed_at": claim.claimed_at.isoformat() if claim.claimed_at else None,
        "lease_expires_at": claim.lease_expires_at.isoformat() if claim.lease_expires_at else None,
        "is_active": claim.is_active,
        "version": getattr(claim, "version", 1),
        "updated_at": (claim.updated_at.isoformat() if claim.updated_at else None)
        if getattr(claim, "updated_at", None) else None,
        "deleted_at": (claim.deleted_at.isoformat() if claim.deleted_at else None)
        if getattr(claim, "deleted_at", None) else None,
    }


async def flow_step_sync_payload(db: AsyncSession, step: FlowStep) -> Dict[str, Any]:
    """Build sync payload for a flow step."""
    flow_public_id = None
    item_public_id = None
    if step.flow_id:
        flow_result = await db.execute(select(Flow).where(Flow.id == step.flow_id))
        f = flow_result.scalar_one_or_none()
        if f:
            flow_public_id = f.public_id
    if step.checklist_item_id:
        item_result = await db.execute(
            select(ChecklistItem).where(ChecklistItem.id == step.checklist_item_id)
        )
        item = item_result.scalar_one_or_none()
        if item:
            item_public_id = item.public_id
    return {
        "public_id": step.public_id,
        "flow_id": step.flow_id,
        "flow_public_id": flow_public_id,
        "checklist_item_id": step.checklist_item_id,
        "item_public_id": item_public_id,
        "order_index": step.order_index,
        "input_mapping": step.input_mapping or {},
        "condition": step.condition,
        "on_failure": step.on_failure or "stop",
        "timeout_override": step.timeout_override,
        "ui_position": step.ui_position or {},
    }


@dataclass
class IngestResult:
    """Result summary for remote event ingestion."""

    applied: int = 0
    skipped: int = 0
    latest_logical_ts: int = 0
    min_deferred_ts: Optional[int] = None


class SyncService:
    """Event-log based sync helper."""

    def __init__(self) -> None:
        self._ingest_locks: dict[str, asyncio.Lock] = {}
        self._ingest_locks_guard = asyncio.Lock()

    async def _ingest_lock_for(self, peer_node_id: str) -> asyncio.Lock:
        """Per-peer ingest lock. Keyspace is bounded (one key per peer), so no eviction."""
        async with self._ingest_locks_guard:
            lock = self._ingest_locks.get(peer_node_id)
            if lock is None:
                lock = asyncio.Lock()
                self._ingest_locks[peer_node_id] = lock
            return lock

    async def get_cursor(self, db: AsyncSession, peer_node_id: str, direction: str) -> int:
        result = await db.execute(
            select(SyncCursor).where(
                SyncCursor.peer_node_id == peer_node_id,
                SyncCursor.direction == direction,
            )
        )
        row = result.scalar_one_or_none()
        return row.last_logical_ts if row else 0

    async def set_cursor(
        self,
        db: AsyncSession,
        *,
        peer_node_id: str,
        direction: str,
        logical_ts: int,
    ) -> None:
        result = await db.execute(
            select(SyncCursor).where(
                SyncCursor.peer_node_id == peer_node_id,
                SyncCursor.direction == direction,
            )
        )
        row = result.scalar_one_or_none()
        if row is None:
            row = SyncCursor(
                peer_node_id=peer_node_id,
                direction=direction,
                last_logical_ts=logical_ts,
            )
            db.add(row)
        else:
            row.last_logical_ts = max(row.last_logical_ts, logical_ts)
        await db.flush()

    async def record_event(
        self,
        db: AsyncSession,
        *,
        entity_type: str,
        entity_public_id: str,
        operation: str,
        payload: Dict[str, Any],
        logical_ts: Optional[int] = None,
        source_node_id: Optional[str] = None,
        event_id: Optional[str] = None,
    ) -> SyncEvent:
        """Create local sync event row."""
        event = SyncEvent(
            event_id=event_id or str(uuid.uuid4()),
            entity_type=entity_type,
            entity_public_id=entity_public_id,
            operation=operation,
            payload=payload,
            logical_ts=logical_ts or _now_ts(),
            source_node_id=source_node_id or settings.NODE_ID,
        )
        db.add(event)
        await db.flush()
        return event

    async def pull_events(
        self,
        db: AsyncSession,
        *,
        since_logical_ts: int,
        limit: int,
        project_public_id: Optional[str] = None,
    ) -> list[SyncEvent]:
        """Return local events newer than cursor, optionally filtered by project."""
        stmt = select(SyncEvent).where(SyncEvent.logical_ts > since_logical_ts)
        if project_public_id:
            stmt = stmt.where(
                or_(
                    (SyncEvent.entity_type == "projects")
                    & (SyncEvent.entity_public_id == project_public_id),
                    SyncEvent.payload["project_public_id"].as_string() == project_public_id,
                )
            )
        result = await db.execute(
            stmt.order_by(SyncEvent.logical_ts.asc(), SyncEvent.source_node_id.asc()).limit(limit)
        )
        return list(result.scalars().all())

    async def ingest_remote_events(
        self,
        db: AsyncSession,
        *,
        peer_node_id: str,
        events: Iterable[Dict[str, Any]],
    ) -> IngestResult:
        """Ingest remote events with dedupe and deterministic apply ordering.
        Events are sorted by logical_ts, source_node_id, and entity type so that
        FK dependencies are respected (e.g. projects before project_members).
        """
        sorted_events = sorted(list(events), key=_ingest_sort_key)
        # File IO before any DB write: a slow/hung host agent must never hold the
        # write txn (WAL single-writer) hostage.
        await self._mkdir_projects_for_events(sorted_events)
        # Whole-batch retry on SQLITE_BUSY. Overlapping ingests can hit
        # BUSY_SNAPSHOT upgrading a DEFERRED txn to a write (busy_timeout does
        # not cover snapshot conflicts). Ingest is idempotent, so rollback and
        # redo from a fresh snapshot converges.
        last_exc: Optional[OperationalError] = None
        for attempt in range(_MAX_INGEST_ATTEMPTS):
            try:
                return await self._ingest_attempt(
                    db, peer_node_id=peer_node_id, sorted_events=sorted_events
                )
            except OperationalError as exc:
                last_exc = exc
                await db.rollback()
                logger.info(f"[Sync] Ingest hit lock contention, retrying ({attempt + 1}/{_MAX_INGEST_ATTEMPTS})")
                await asyncio.sleep(0.1 * (2 ** attempt))
        assert last_exc is not None
        raise last_exc

    async def _ingest_attempt(
        self,
        db: AsyncSession,
        *,
        peer_node_id: str,
        sorted_events: list[Dict[str, Any]],
    ) -> IngestResult:
        """One ingest attempt: prefetch, locked multi-pass apply, cursor write."""
        result = IngestResult()
        # Two SELECTs replace the per-event dedupe + latest lookups below.
        prefetch = await self._prefetch_batch_state(db, sorted_events)
        # Smallest logical_ts of an event we had to DEFER (parent not synced yet). We
        # cap the cursor just below it so deferred events are re-pulled next cycle
        # instead of being silently skipped forever.
        min_deferred: Optional[int] = None

        # Bounded multi-pass: a child sorted before its parent defers on the first
        # pass and applies on a later pass once the parent exists. Stops early when
        # nothing deferred or no progress was made (missing parent isn't in the batch).
        # Serialized per peer so concurrent pull+push cycles can't interleave writes
        # and cursor updates for the same peer.
        peer_lock = await self._ingest_lock_for(peer_node_id)
        async with peer_lock:
            pending = sorted_events
            for _ in range(_MAX_INGEST_PASSES):
                pass_deferred: list[Dict[str, Any]] = []
                pass_min_deferred: Optional[int] = None
                for incoming in pending:
                    deferred_ts = await self._ingest_one(
                        db, incoming=incoming, result=result, prefetch=prefetch
                    )
                    if deferred_ts is not None:
                        pass_deferred.append(incoming)
                        pass_min_deferred = (
                            deferred_ts if pass_min_deferred is None
                            else min(pass_min_deferred, deferred_ts)
                        )
                min_deferred = pass_min_deferred
                if not pass_deferred or len(pass_deferred) == len(pending):
                    break
                pending = pass_deferred

            result.min_deferred_ts = min_deferred
            cursor_ts = result.latest_logical_ts
            if min_deferred is not None:
                # Don't advance past the earliest deferred event, so it (and everything after)
                # is retried once its parent has synced.
                cursor_ts = min(cursor_ts, min_deferred - 1)
            await self.set_cursor(
                db,
                peer_node_id=peer_node_id,
                direction="pull",
                logical_ts=cursor_ts,
            )
        return result

    async def _prefetch_batch_state(
        self, db: AsyncSession, events: list[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Load dedupe + latest-entity state for a batch in two SELECTs.

        Callers keep the returned structures fresh as the batch applies (see
        `_ingest_one`), so later events in the batch compare against records
        written earlier in the same batch.
        """
        ids: list[str] = []
        pairs: set[tuple[str, str]] = set()
        for e in events:
            if not isinstance(e, dict):
                continue
            if e.get("event_id"):
                ids.append(e["event_id"])
            if e.get("entity_type") and e.get("entity_public_id"):
                pairs.add((e["entity_type"], e["entity_public_id"]))
        seen_ids: set[str] = set()
        if ids:
            rows = await db.execute(select(SyncEvent.event_id).where(SyncEvent.event_id.in_(ids)))
            seen_ids = set(rows.scalars().all())
        latest_by_entity: Dict[tuple[str, str], SyncEvent] = {}
        if pairs:
            rows = await db.execute(
                select(SyncEvent)
                .where(tuple_(SyncEvent.entity_type, SyncEvent.entity_public_id).in_(pairs))
                .order_by(
                    SyncEvent.logical_ts.desc(),
                    SyncEvent.source_node_id.desc(),
                    SyncEvent.event_id.desc(),
                )
            )
            for row in rows.scalars().all():
                latest_by_entity.setdefault((row.entity_type, row.entity_public_id), row)
        return {"seen_ids": seen_ids, "latest_by_entity": latest_by_entity}

    async def _ingest_one(
        self,
        db: AsyncSession,
        *,
        incoming: Dict[str, Any],
        result: IngestResult,
        prefetch: Optional[Dict[str, Any]] = None,
    ) -> Optional[int]:
        """Ingest a single event. Returns its logical_ts if deferred, else None."""
        try:
            event_id = incoming["event_id"]
            logical_ts = int(incoming["logical_ts"])
            source_node_id = str(incoming["source_node_id"])
            entity_type = str(incoming["entity_type"])
            entity_public_id = str(incoming["entity_public_id"])
            operation = str(incoming["operation"])
        except (KeyError, TypeError, ValueError) as exc:
            # Poison envelope: count it and move on. Never recorded, never
            # deferred (no valid ts to pin the cursor with) — one bad event
            # must not stall the peer.
            result.skipped += 1
            logger.info(f"[Sync] Skipping malformed event: {exc}")
            return None
        payload = incoming.get("payload") or {}

        if prefetch is not None:
            already_seen = event_id in prefetch["seen_ids"]
        else:
            exists = await db.execute(select(SyncEvent.id).where(SyncEvent.event_id == event_id))
            already_seen = exists.scalar_one_or_none() is not None
        if already_seen:
            result.skipped += 1
            result.latest_logical_ts = max(result.latest_logical_ts, logical_ts)
            return None

        if prefetch is not None:
            latest = prefetch["latest_by_entity"].get((entity_type, entity_public_id))
        else:
            latest_result = await db.execute(
                select(SyncEvent)
                .where(
                    SyncEvent.entity_type == entity_type,
                    SyncEvent.entity_public_id == entity_public_id,
                )
                .order_by(
                    SyncEvent.logical_ts.desc(),
                    SyncEvent.source_node_id.desc(),
                    SyncEvent.event_id.desc(),
                )
                .limit(1)
            )
            latest = latest_result.scalar_one_or_none()
        incoming_key = _event_key(logical_ts, source_node_id, event_id)
        latest_key = (
            _event_key(latest.logical_ts, latest.source_node_id, latest.event_id)
            if latest else None
        )

        # Apply FIRST, record only if it applied — a deferred event isn't marked
        # processed (which would make the dedupe check skip it permanently), and
        # a stale LWW-loser isn't logged either (it would only bloat the table
        # and amplify re-pushes). Seen-but-stale still advances the cursor.
        # Each event runs in its own savepoint: a unique-violation race with a
        # concurrent ingester rolls back just this event, never the batch.
        try:
            async with db.begin_nested():
                deferred = False
                applied_now = False
                if latest_key is None or incoming_key > latest_key:
                    try:
                        await self._apply_event(db, entity_type=entity_type, operation=operation, payload=payload)
                        applied_now = True
                    except _FKNotReady as exc:
                        deferred = True
                        logger.info(f"[Sync] Deferring {entity_type} {entity_public_id} for retry: {exc}")
                else:
                    result.skipped += 1

                if deferred:
                    return logical_ts
                if applied_now:
                    recorded = await self.record_event(
                        db,
                        entity_type=entity_type,
                        entity_public_id=entity_public_id,
                        operation=operation,
                        payload=payload,
                        logical_ts=logical_ts,
                        source_node_id=source_node_id,
                        event_id=event_id,
                    )
                    result.applied += 1
                    if prefetch is not None:
                        # Keep batch state fresh: later events (other versions
                        # of this entity, later passes) compare against this row.
                        prefetch["seen_ids"].add(event_id)
                        key = (entity_type, entity_public_id)
                        current = prefetch["latest_by_entity"].get(key)
                        current_key = (
                            _event_key(current.logical_ts, current.source_node_id, current.event_id)
                            if current is not None else None
                        )
                        if current_key is None or incoming_key > current_key:
                            prefetch["latest_by_entity"][key] = recorded
                result.latest_logical_ts = max(result.latest_logical_ts, logical_ts)
                return None
        except IntegrityError:
            # Lost a race (event or entity row committed by a concurrent
            # ingester between our check and our flush): re-check. Recorded by
            # the winner means a plain skip; otherwise skip+log so one bad row
            # never nukes its batch.
            exists = await db.execute(select(SyncEvent.id).where(SyncEvent.event_id == event_id))
            if exists.scalar_one_or_none() is None:
                logger.info(f"[Sync] Skipping event that violates constraints: {entity_type} {entity_public_id}")
            result.skipped += 1
            result.latest_logical_ts = max(result.latest_logical_ts, logical_ts)
            return None

    async def _mkdir_projects_for_events(self, events: list[Dict[str, Any]]) -> None:
        """Best-effort mkdir for projects in this batch, once per distinct name.

        Runs BEFORE any DB write. Idempotent, so poison/loser events in the
        batch only cost an empty dir, never correctness.
        """
        names: set[str] = set()
        for e in events:
            if not isinstance(e, dict) or e.get("entity_type") != "projects":
                continue
            name = (e.get("payload") or {}).get("name")
            if name:
                names.add(name)
        for name in sorted(names):
            try:
                from app.core.utils import get_project_path
                from app.core.host_runner import host_runner
                path = get_project_path(name)
                result = await host_runner.execute_sync(f"mkdir -p {shlex.quote(path)}")
                if result.get("exit_code") != 0:
                    logger.error(f"[Sync] Failed to create project directory {path}: {result.get('stderr', '')}")
            except Exception as exc:
                logger.error(f"[Sync] Error creating project directory for {name}: {exc}")

    async def _apply_event(
        self,
        db: AsyncSession,
        *,
        entity_type: str,
        operation: str,
        payload: Dict[str, Any],
    ) -> None:
        """Apply supported remote events to local DB."""
        if entity_type == "users":
            await self._apply_user(db, payload=payload)
            return
        if entity_type == "projects":
            await self._apply_project(db, payload=payload)
            return
        if entity_type == "project_members":
            await self._apply_project_member(db, payload=payload)
            return
        if entity_type == "checklist_groups":
            if operation == "delete":
                await self._apply_checklist_group_delete(db, payload=payload)
            else:
                await self._apply_checklist_group(db, payload=payload)
            return
        if entity_type == "checklist_items":
            if operation == "delete":
                await self._apply_checklist_item_delete(db, payload=payload)
            else:
                await self._apply_checklist_item(db, payload=payload)
            return
        if entity_type == "flows":
            if operation == "delete":
                await self._apply_flow_delete(db, payload=payload)
            else:
                await self._apply_flow(db, payload=payload)
            return
        if entity_type == "flow_steps":
            if operation == "delete":
                await self._apply_flow_step_delete(db, payload=payload)
            else:
                await self._apply_flow_step(db, payload=payload)
            return
        if entity_type == "findings":
            await self._apply_finding(db, payload=payload)
            return
        if entity_type == "hosts":
            await self._apply_host(db, payload=payload)
            return
        if entity_type == "services":
            await self._apply_service(db, payload=payload)
            return
        if entity_type == "project_variables":
            await self._apply_project_variable(db, payload=payload)
            return
        if entity_type == "executions":
            if operation == "delete":
                await self._apply_execution_delete(db, payload=payload)
            else:
                await self._apply_execution(db, payload=payload)
            return
        if entity_type == "discovered_files":
            if operation == "delete":
                await self._apply_discovered_file_delete(db, payload=payload)
            else:
                await self._apply_discovered_file(db, payload=payload)
            return
        if entity_type == "checklist_claims":
            await self._apply_checklist_claim(db, operation=operation, payload=payload)

    async def _apply_user(self, db: AsyncSession, *, payload: Dict[str, Any]) -> None:
        public_id = payload.get("public_id")
        if not public_id:
            return
        result = await db.execute(select(User).where(User.public_id == public_id))
        user = result.scalar_one_or_none()
        if user is None:
            user = User(
                public_id=public_id,
                username=payload.get("username", f"user-{public_id[:8]}"),
                email=payload.get("email", f"{public_id[:8]}@local.invalid"),
                password_hash=payload.get("password_hash") or "",
                is_active=bool(payload.get("is_active", True)),
                role=payload.get("role", "operator"),
                must_change_password=bool(payload.get("must_change_password", False)),
            )
            db.add(user)
        user.username = payload.get("username", user.username)
        user.email = payload.get("email", user.email)
        if payload.get("password_hash"):
            user.password_hash = payload["password_hash"]
        user.is_active = bool(payload.get("is_active", user.is_active))
        if "role" in payload:
            user.role = payload["role"]
        if "must_change_password" in payload:
            user.must_change_password = bool(payload["must_change_password"])
        user.updated_at = _parse_dt(payload.get("updated_at")) or utcnow()

    async def _apply_project(self, db: AsyncSession, *, payload: Dict[str, Any]) -> None:
        public_id = payload.get("public_id")
        if not public_id:
            return
        result = await db.execute(select(Project).where(Project.public_id == public_id))
        project = result.scalar_one_or_none()
        if project is None:
            project = Project(
                public_id=public_id,
                name=payload.get("name", f"Synced-{public_id[:8]}"),
                scope=payload.get("scope") or {},
            )
            db.add(project)

        _existing_status = getattr(project, "status", None)
        status_value = payload.get(
            "status",
            _existing_status.value if _existing_status is not None else "planning",
        )
        if status_value is None:
            status_value = "planning"
        try:
            project.status = ProjectStatus(status_value)
        except Exception:
            pass  # unknown status from a newer peer: keep existing, don't clobber

        project.name = payload.get("name", project.name)
        _apply_present(project, payload, ("description", "scope", "extra_data"))
        if payload.get("created_by_user_public_id"):
            user_result = await db.execute(
                select(User).where(User.public_id == payload["created_by_user_public_id"])
            )
            owner = user_result.scalar_one_or_none()
            if owner is not None:
                project.created_by_user_id = owner.id
        project.start_date = _parse_dt(payload.get("start_date"))
        project.end_date = _parse_dt(payload.get("end_date"))
        project.deleted_at = _parse_dt(payload.get("deleted_at"))
        project.updated_at = _parse_dt(payload.get("updated_at")) or utcnow()

        # Record the project path (pure). The actual mkdir runs in the ingest
        # pre-pass, BEFORE the write txn, so a slow host agent never holds it.
        from app.core.utils import get_project_path
        if project.extra_data is None:
            project.extra_data = {}
        project.extra_data["project_path"] = get_project_path(project.name)

    async def _apply_project_member(self, db: AsyncSession, *, payload: Dict[str, Any]) -> None:
        public_id = payload.get("public_id")
        if not public_id:
            return

        project_id = None
        user_id = None
        project_public_id = payload.get("project_public_id")
        user_public_id = payload.get("user_public_id")

        if project_public_id:
            project_result = await db.execute(select(Project).where(Project.public_id == project_public_id))
            project = project_result.scalar_one_or_none()
            project_id = project.id if project else None
        if user_public_id:
            user_result = await db.execute(select(User).where(User.public_id == user_public_id))
            user = user_result.scalar_one_or_none()
            user_id = user.id if user else None
        if project_id is None or user_id is None:
            if not project_public_id and not user_public_id:
                return  # no refs at all: unplaceable, retry cannot help
            missing = []
            if project_id is None:
                missing.append(f"project {project_public_id}")
            if user_id is None:
                missing.append(f"user {user_public_id}")
            raise _FKNotReady(f"project_member {public_id}: {', '.join(missing)} not present yet")

        result = await db.execute(select(ProjectMember).where(ProjectMember.public_id == public_id))
        member = result.scalar_one_or_none()
        if member is None:
            # Same (project_id, user_id) may already exist with another public_id (other node or local).
            existing_result = await db.execute(
                select(ProjectMember).where(
                    ProjectMember.project_id == project_id,
                    ProjectMember.user_id == user_id,
                )
            )
            existing = existing_result.scalar_one_or_none()
            if existing is not None:
                member = existing
                member.public_id = public_id
            else:
                member = ProjectMember(
                    public_id=public_id,
                    project_id=project_id,
                    user_id=user_id,
                )
                db.add(member)
        else:
            member.project_id = project_id
            member.user_id = user_id

        member.deleted_at = _parse_dt(payload.get("deleted_at"))
        member.updated_at = _parse_dt(payload.get("updated_at")) or utcnow()

    async def _apply_checklist_group(self, db: AsyncSession, *, payload: Dict[str, Any]) -> None:
        public_id = payload.get("public_id")
        if not public_id:
            return

        project_id = None
        if payload.get("project_public_id"):
            project_result = await db.execute(
                select(Project).where(Project.public_id == payload["project_public_id"])
            )
            project = project_result.scalar_one_or_none()
            project_id = project.id if project else None
            if project_id is None:
                raise _FKNotReady(
                    f"checklist_group {public_id}: project {payload['project_public_id']} not present yet"
                )

        result = await db.execute(select(ChecklistGroup).where(ChecklistGroup.public_id == public_id))
        group = result.scalar_one_or_none()
        if group is None:
            if project_id is None:
                return  # no project ref at all: cannot place the row
            group = ChecklistGroup(
                public_id=public_id,
                project_id=project_id,
                name=payload.get("name", f"SyncedGroup-{public_id[:8]}"),
            )
            db.add(group)

        if project_id is not None:
            group.project_id = project_id
        group.name = payload.get("name", group.name)
        group.description = payload.get("description")
        group.order_index = int(payload.get("order_index", group.order_index) or 0)
        group.icon = payload.get("icon")
        group.color = payload.get("color")
        group.collapsed = bool(payload.get("collapsed", group.collapsed))
        group.is_template = bool(payload.get("is_template", group.is_template))
        group.is_trashed = bool(payload.get("is_trashed", group.is_trashed))
        group.deleted_at = _parse_dt(payload.get("deleted_at"))
        group.updated_at = _parse_dt(payload.get("updated_at")) or utcnow()

    async def _apply_checklist_item(self, db: AsyncSession, *, payload: Dict[str, Any]) -> None:
        public_id = payload.get("public_id")
        if not public_id:
            return

        group_id = None
        if payload.get("group_public_id"):
            group_result = await db.execute(
                select(ChecklistGroup).where(ChecklistGroup.public_id == payload["group_public_id"])
            )
            group = group_result.scalar_one_or_none()
            group_id = group.id if group else None
            if group_id is None:
                raise _FKNotReady(
                    f"checklist_item {public_id}: group {payload['group_public_id']} not present yet"
                )
        if group_id is None:
            return

        result = await db.execute(select(ChecklistItem).where(ChecklistItem.public_id == public_id))
        item = result.scalar_one_or_none()
        if item is None:
            item = ChecklistItem(
                public_id=public_id,
                group_id=group_id,
                name=payload.get("name", f"SyncedItem-{public_id[:8]}"),
                command_template=payload.get("command_template", ""),
            )
            db.add(item)

        item.group_id = group_id
        item.name = payload.get("name", item.name)
        # Only assign what the peer actually sent: an absent key (older node, partial
        # event) must not wipe a good local value. An explicitly-sent empty value still
        # clears, which is the peer's intent.
        _apply_present(
            item, payload,
            ("description", "output_regex", "variables", "input_definitions",
             "storage_policy", "parameter_schema", "target_filter", "alert_patterns",
             "finding_template", "tags"),
        )
        if payload.get("command_template"):
            item.command_template = payload["command_template"]
        item.timeout = int(payload.get("timeout", item.timeout) or 3600)
        item.enabled = bool(payload.get("enabled", item.enabled))
        item.order_index = int(payload.get("order_index", item.order_index) or 0)
        item.is_trashed = bool(payload.get("is_trashed", item.is_trashed))
        item.deleted_at = _parse_dt(payload.get("deleted_at"))
        item.updated_at = _parse_dt(payload.get("updated_at")) or utcnow()

    async def _apply_checklist_group_delete(self, db: AsyncSession, *, payload: Dict[str, Any]) -> None:
        """Apply checklist group delete: remove row so peer state matches."""
        public_id = payload.get("public_id")
        if not public_id:
            return
        result = await db.execute(select(ChecklistGroup).where(ChecklistGroup.public_id == public_id))
        group = result.scalar_one_or_none()
        if group:
            await db.delete(group)

    async def _apply_checklist_item_delete(self, db: AsyncSession, *, payload: Dict[str, Any]) -> None:
        """Apply checklist item delete: remove row so peer state matches."""
        public_id = payload.get("public_id")
        if not public_id:
            return
        result = await db.execute(select(ChecklistItem).where(ChecklistItem.public_id == public_id))
        item = result.scalar_one_or_none()
        if item:
            await db.delete(item)

    async def _apply_host(self, db: AsyncSession, *, payload: Dict[str, Any]) -> None:
        public_id = payload.get("public_id")
        if not public_id:
            return

        project_id = None
        if payload.get("project_public_id"):
            project_result = await db.execute(
                select(Project).where(Project.public_id == payload["project_public_id"])
            )
            project = project_result.scalar_one_or_none()
            project_id = project.id if project else None
            if project_id is None:
                # Parent project hasn't synced yet (clock skew) — defer for retry, don't
                # silently drop the host event.
                raise _FKNotReady(f"host {public_id}: project {payload['project_public_id']} not present yet")
        if project_id is None:
            return

        result = await db.execute(select(Host).where(Host.public_id == public_id))
        host = result.scalar_one_or_none()
        if host is None:
            host = Host(public_id=public_id, project_id=project_id)
            db.add(host)

        host.project_id = project_id
        host.status = payload.get("status", host.status)
        # extra_data["domain"] and tags drive domain-scoped variables and tag-based target
        # resolution — losing them to an absent payload key breaks executions on this node.
        _apply_present(
            host, payload,
            ("ip_address", "hostname", "fqdn", "os_info", "notes", "extra_data", "tags"),
        )
        # ROE carve-out: every node must agree or one peer happily scans a host another
        # peer put out of scope. Coerced rather than routed through _apply_present, whose
        # None-normalisation only knows how to empty dicts/lists — a null here would hit
        # a NOT NULL column.
        if "excluded" in payload:
            host.excluded = bool(payload["excluded"])
        host.deleted_at = _parse_dt(payload.get("deleted_at"))
        host.updated_at = _parse_dt(payload.get("updated_at")) or utcnow()

    async def _apply_project_variable(self, db: AsyncSession, *, payload: Dict[str, Any]) -> None:
        public_id = payload.get("public_id")
        if not public_id:
            return

        project_id = None
        host_id = None
        if payload.get("project_public_id"):
            project_result = await db.execute(
                select(Project).where(Project.public_id == payload["project_public_id"])
            )
            project = project_result.scalar_one_or_none()
            project_id = project.id if project else None
        if payload.get("host_public_id"):
            host_result = await db.execute(
                select(Host).where(Host.public_id == payload["host_public_id"])
            )
            host = host_result.scalar_one_or_none()
            host_id = host.id if host else None
        if project_id is None:
            if payload.get("project_public_id"):
                raise _FKNotReady(
                    f"project_variable {public_id}: project {payload['project_public_id']} not present yet"
                )
            return

        result = await db.execute(select(ProjectVariable).where(ProjectVariable.public_id == public_id))
        variable = result.scalar_one_or_none()
        key = payload.get("key") or "synced"
        if variable is None:
            existing_result = await db.execute(
                select(ProjectVariable).where(
                    ProjectVariable.project_id == project_id,
                    ProjectVariable.key == key,
                )
            )
            existing = existing_result.scalar_one_or_none()
            if existing is not None:
                variable = existing
                variable.public_id = public_id
            else:
                # Only use payload value when key present so empty/None sync correctly
                val = payload["value"] if "value" in payload else None
                variable = ProjectVariable(
                    public_id=public_id,
                    project_id=project_id,
                    key=key,
                    value=val,
                )
                db.add(variable)

        variable.project_id = project_id
        variable.host_id = host_id or variable.host_id
        variable.key = key
        if "value" in payload:
            variable.value = payload["value"]
        variable.var_type = payload.get("var_type", variable.var_type)
        variable.deleted_at = _parse_dt(payload.get("deleted_at"))
        variable.updated_at = _parse_dt(payload.get("updated_at")) or utcnow()

    async def _apply_execution(self, db: AsyncSession, *, payload: Dict[str, Any]) -> None:
        public_id = payload.get("public_id")
        if not public_id:
            return

        item_id = None
        host_id = None
        if payload.get("item_public_id"):
            item_result = await db.execute(
                select(ChecklistItem).where(ChecklistItem.public_id == payload["item_public_id"])
            )
            item = item_result.scalar_one_or_none()
            item_id = item.id if item else None

        if payload.get("host_public_id"):
            host_result = await db.execute(
                select(Host).where(Host.public_id == payload["host_public_id"])
            )
            host = host_result.scalar_one_or_none()
            host_id = host.id if host else None

        result = await db.execute(select(Execution).where(Execution.public_id == public_id))
        execution = result.scalar_one_or_none()
        if execution is None:
            # Defer if the item ref is present but not synced yet; only truly skip when
            # there's no item reference at all.
            if item_id is None:
                if payload.get("item_public_id"):
                    raise _FKNotReady(f"execution {public_id}: item {payload['item_public_id']} not present yet")
                return
            execution = Execution(
                public_id=public_id,
                item_id=item_id,
                host_id=host_id,
                command=payload.get("command") or "(synced)",
                variables_used=payload.get("variables_used") or {},
                version=int(payload.get("version", 1)),
            )
            db.add(execution)
        else:
            execution.item_id = item_id or execution.item_id
            # D5: don't null an existing execution's host link when the host ref is
            # merely unresolved (host event not synced yet).
            execution.host_id = host_id or execution.host_id

        _existing_status = getattr(execution, "status", None)
        status_value = payload.get(
            "status",
            _existing_status.value if _existing_status is not None else "pending",
        )
        if status_value is None:
            status_value = "pending"
        try:
            execution.status = ExecutionStatus(status_value)
        except Exception:
            pass
        execution.deleted_at = _parse_dt(payload.get("deleted_at"))
        execution.updated_at = _parse_dt(payload.get("updated_at")) or utcnow()
        if payload.get("stdout") is not None:
            execution.stdout = payload.get("stdout")
        if payload.get("stderr") is not None:
            execution.stderr = payload.get("stderr")
        if payload.get("exit_code") is not None:
            execution.exit_code = payload.get("exit_code")
        if payload.get("started_at") is not None:
            execution.started_at = _parse_dt(payload.get("started_at"))
        if payload.get("completed_at") is not None:
            execution.completed_at = _parse_dt(payload.get("completed_at"))

    async def _apply_execution_delete(
        self, db: AsyncSession, *, payload: Dict[str, Any]
    ) -> None:
        """Apply execution delete: remove row so peer state matches."""
        public_id = payload.get("public_id")
        if not public_id:
            return
        result = await db.execute(select(Execution).where(Execution.public_id == public_id))
        execution = result.scalar_one_or_none()
        if execution:
            await db.delete(execution)

    async def _apply_service(self, db: AsyncSession, *, payload: Dict[str, Any]) -> None:
        public_id = payload.get("public_id")
        if not public_id:
            return
        host_id = None
        if payload.get("host_public_id"):
            host_result = await db.execute(
                select(Host).where(Host.public_id == payload["host_public_id"])
            )
            host = host_result.scalar_one_or_none()
            host_id = host.id if host else None
            if host_id is None:
                raise _FKNotReady(
                    f"service {public_id}: host {payload['host_public_id']} not present yet"
                )
        if host_id is None:
            return
        result = await db.execute(select(Service).where(Service.public_id == public_id))
        service = result.scalar_one_or_none()
        if service is None:
            service = Service(
                public_id=public_id,
                host_id=host_id,
                port=int(payload.get("port", 0)),
                protocol=payload.get("protocol", "tcp"),
                name=payload.get("name"),
                version=payload.get("version"),
                product=payload.get("product"),
                state=payload.get("state", "open"),
                banner=payload.get("banner"),
                ssl=bool(payload.get("ssl", False)),
                extra_data=payload.get("extra_data") or {},
            )
            db.add(service)
        else:
            service.host_id = host_id
            service.port = int(payload.get("port", service.port))
            service.protocol = payload.get("protocol", service.protocol)
            if _sync_should_apply_service_name(service.name, payload.get("name")):
                service.name = payload.get("name")
            if payload.get("version"):
                service.version = payload.get("version")
            if payload.get("product"):
                service.product = payload.get("product")
            service.state = payload.get("state", service.state)
            if "banner" in payload:
                service.banner = payload["banner"]
            service.ssl = bool(payload.get("ssl", service.ssl))
            if payload.get("extra_data") is not None:
                service.extra_data = payload.get("extra_data") or {}

    async def _apply_discovered_file(self, db: AsyncSession, *, payload: Dict[str, Any]) -> None:
        public_id = payload.get("public_id")
        if not public_id:
            return
        host_id = None
        execution_id = None
        if payload.get("host_public_id"):
            host_result = await db.execute(
                select(Host).where(Host.public_id == payload["host_public_id"])
            )
            host = host_result.scalar_one_or_none()
            host_id = host.id if host else None
        if payload.get("execution_public_id"):
            exec_result = await db.execute(
                select(Execution).where(Execution.public_id == payload["execution_public_id"])
            )
            ex = exec_result.scalar_one_or_none()
            execution_id = ex.id if ex else None
        if host_id is None:
            if payload.get("host_public_id"):
                raise _FKNotReady(
                    f"discovered_file {public_id}: host {payload['host_public_id']} not present yet"
                )
            return
        result = await db.execute(
            select(DiscoveredFile).where(DiscoveredFile.public_id == public_id)
        )
        df = result.scalar_one_or_none()
        if df is None:
            df = DiscoveredFile(
                public_id=public_id,
                host_id=host_id,
                execution_id=execution_id,
                share_name=payload.get("share_name", ""),
                path=payload.get("path", "/"),
                name=payload.get("name", ""),
                file_type=payload.get("file_type", "file"),
                size=payload.get("size"),
                permissions=payload.get("permissions") or {},
                is_readable=bool(payload.get("is_readable", True)),
                is_writable=bool(payload.get("is_writable", False)),
                is_executable=bool(payload.get("is_executable", False)),
                is_interesting=bool(payload.get("is_interesting", False)),
                interest_reason=payload.get("interest_reason"),
                content_preview=payload.get("content_preview"),
                extra_data=payload.get("extra_data") or {},
            )
            db.add(df)
        else:
            df.host_id = host_id
            df.execution_id = execution_id or df.execution_id
            df.share_name = payload.get("share_name", df.share_name)
            df.path = payload.get("path", df.path)
            df.name = payload.get("name", df.name)
            df.file_type = payload.get("file_type", df.file_type)
            df.size = payload.get("size")
            df.permissions = payload.get("permissions") or {}
            df.is_readable = bool(payload.get("is_readable", True))
            df.is_writable = bool(payload.get("is_writable", False))
            df.is_interesting = bool(payload.get("is_interesting", False))
            df.interest_reason = payload.get("interest_reason")
            df.content_preview = payload.get("content_preview")
            df.extra_data = payload.get("extra_data") or {}

    async def _apply_discovered_file_delete(
        self, db: AsyncSession, *, payload: Dict[str, Any]
    ) -> None:
        public_id = payload.get("public_id")
        if not public_id:
            return
        result = await db.execute(
            select(DiscoveredFile).where(DiscoveredFile.public_id == public_id)
        )
        df = result.scalar_one_or_none()
        if df:
            await db.delete(df)

    async def _apply_flow(self, db: AsyncSession, *, payload: Dict[str, Any]) -> None:
        public_id = payload.get("public_id")
        if not public_id:
            return
        project_id = None
        if payload.get("project_public_id"):
            proj_result = await db.execute(
                select(Project).where(Project.public_id == payload["project_public_id"])
            )
            project = proj_result.scalar_one_or_none()
            project_id = project.id if project else None
            if project_id is None:
                raise _FKNotReady(
                    f"flow {public_id}: project {payload['project_public_id']} not present yet"
                )
        result = await db.execute(select(Flow).where(Flow.public_id == public_id))
        flow = result.scalar_one_or_none()
        if flow is None:
            if project_id is None:
                return  # no project ref at all: cannot place the row
            flow = Flow(
                public_id=public_id,
                project_id=project_id,
                name=payload.get("name", "SyncedFlow"),
                description=payload.get("description"),
                is_template=bool(payload.get("is_template", False)),
                flow_definition=payload.get("flow_definition") or {},
                tags=payload.get("tags") or [],
            )
            db.add(flow)
        else:
            if project_id is not None:
                flow.project_id = project_id
            flow.name = payload.get("name", flow.name)
            flow.is_template = bool(payload.get("is_template", flow.is_template))
            _apply_present(flow, payload, ("description", "flow_definition", "tags"))
        flow.updated_at = _parse_dt(payload.get("updated_at")) or utcnow()

    async def _apply_flow_delete(self, db: AsyncSession, *, payload: Dict[str, Any]) -> None:
        public_id = payload.get("public_id")
        if not public_id:
            return
        result = await db.execute(select(Flow).where(Flow.public_id == public_id))
        flow = result.scalar_one_or_none()
        if flow:
            await db.delete(flow)

    async def _apply_flow_step(self, db: AsyncSession, *, payload: Dict[str, Any]) -> None:
        public_id = payload.get("public_id")
        if not public_id:
            return
        flow_id = None
        checklist_item_id = None
        if payload.get("flow_public_id"):
            flow_result = await db.execute(
                select(Flow).where(Flow.public_id == payload["flow_public_id"])
            )
            flow = flow_result.scalar_one_or_none()
            flow_id = flow.id if flow else None
        if payload.get("item_public_id"):
            item_result = await db.execute(
                select(ChecklistItem).where(ChecklistItem.public_id == payload["item_public_id"])
            )
            item = item_result.scalar_one_or_none()
            checklist_item_id = item.id if item else None
        if flow_id is None or checklist_item_id is None:
            missing = []
            if flow_id is None:
                missing.append(f"flow {payload.get('flow_public_id')}")
            if checklist_item_id is None:
                missing.append(f"item {payload.get('item_public_id')}")
            if payload.get("flow_public_id") or payload.get("item_public_id"):
                raise _FKNotReady(f"flow_step {public_id}: {', '.join(missing)} not present yet")
            return
        result = await db.execute(select(FlowStep).where(FlowStep.public_id == public_id))
        step = result.scalar_one_or_none()
        if step is None:
            step = FlowStep(
                public_id=public_id,
                flow_id=flow_id,
                checklist_item_id=checklist_item_id,
                order_index=int(payload.get("order_index", 0)),
                input_mapping=payload.get("input_mapping") or {},
                condition=payload.get("condition"),
                on_failure=payload.get("on_failure", "stop"),
                timeout_override=payload.get("timeout_override"),
                ui_position=payload.get("ui_position") or {},
            )
            db.add(step)
        else:
            step.flow_id = flow_id
            step.checklist_item_id = checklist_item_id
            step.order_index = int(payload.get("order_index", step.order_index))
            step.on_failure = payload.get("on_failure", step.on_failure)
            # An absent "condition" must not clear the step's gate (that would make a
            # conditional step run unconditionally on this node).
            _apply_present(
                step, payload,
                ("input_mapping", "condition", "timeout_override", "ui_position"),
            )

    async def _apply_flow_step_delete(self, db: AsyncSession, *, payload: Dict[str, Any]) -> None:
        public_id = payload.get("public_id")
        if not public_id:
            return
        result = await db.execute(select(FlowStep).where(FlowStep.public_id == public_id))
        step = result.scalar_one_or_none()
        if step:
            await db.delete(step)

    async def _apply_checklist_claim(
        self,
        db: AsyncSession,
        *,
        operation: str,
        payload: Dict[str, Any],
    ) -> None:
        claim_public_id = payload.get("public_id")
        if not claim_public_id:
            return

        project_id = None
        item_id = None
        host_id = None
        claimed_by_user_id = None

        if payload.get("project_public_id"):
            project_result = await db.execute(
                select(Project).where(Project.public_id == payload["project_public_id"])
            )
            project = project_result.scalar_one_or_none()
            project_id = project.id if project else None

        if payload.get("item_public_id"):
            item_result = await db.execute(
                select(ChecklistItem).where(ChecklistItem.public_id == payload["item_public_id"])
            )
            item = item_result.scalar_one_or_none()
            item_id = item.id if item else None

        if payload.get("host_public_id"):
            host_result = await db.execute(
                select(Host).where(Host.public_id == payload["host_public_id"])
            )
            host = host_result.scalar_one_or_none()
            host_id = host.id if host else None

        if payload.get("claimed_by_user_public_id"):
            user_result = await db.execute(
                select(User).where(User.public_id == payload["claimed_by_user_public_id"])
            )
            c_user = user_result.scalar_one_or_none()
            claimed_by_user_id = c_user.id if c_user else None

        if project_id is None or item_id is None:
            if not payload.get("project_public_id") and not payload.get("item_public_id"):
                return  # no refs at all: unplaceable, retry cannot help
            missing = []
            if project_id is None:
                missing.append(f"project {payload.get('project_public_id')}")
            if item_id is None:
                missing.append(f"item {payload.get('item_public_id')}")
            raise _FKNotReady(f"checklist_claim {claim_public_id}: {', '.join(missing)} not present yet")

        host_scope_key = payload.get("host_scope_key", "global")
        incoming_version = int(payload.get("version", 1))
        claim_result = await db.execute(
            select(ChecklistClaim).where(ChecklistClaim.public_id == claim_public_id)
        )
        claim = claim_result.scalar_one_or_none()
        if claim is None:
            # Dedupe on the natural key: another node may have independently claimed the
            # same (project,item,host_scope_key) with a different public_id. Inserting a
            # second row violates the unique constraint and rolls back the whole ingest
            # batch (permanent sync stall). Adopt the existing row, last-write-wins.
            nk_result = await db.execute(
                select(ChecklistClaim).where(
                    ChecklistClaim.project_id == project_id,
                    ChecklistClaim.item_id == item_id,
                    ChecklistClaim.host_scope_key == host_scope_key,
                )
            )
            existing = nk_result.scalar_one_or_none()
            if existing is not None:
                if incoming_version < (existing.version or 0):
                    return  # our copy is newer; ignore the stale duplicate
                existing.public_id = claim_public_id
                claim = existing
            else:
                claim = ChecklistClaim(
                    public_id=claim_public_id,
                    project_id=project_id,
                    item_id=item_id,
                    host_id=host_id,
                    host_scope_key=host_scope_key,
                    version=incoming_version,
                )
                db.add(claim)

        claim.project_id = project_id
        claim.item_id = item_id
        claim.host_id = host_id
        claim.claimed_by_user_id = claimed_by_user_id
        claim.claimed_at = _parse_dt(payload.get("claimed_at"))
        claim.lease_expires_at = _parse_dt(payload.get("lease_expires_at"))
        claim.is_active = bool(payload.get("is_active", False))
        claim.deleted_at = _parse_dt(payload.get("deleted_at"))
        claim.version = int(payload.get("version", claim.version))
        claim.updated_at = _parse_dt(payload.get("updated_at")) or utcnow()

    async def _apply_finding(self, db: AsyncSession, *, payload: Dict[str, Any]) -> None:
        from app.models.finding import Finding, FindingSeverity, FindingStatus
        public_id = payload.get("public_id")
        if not public_id:
            return
        project_id = None
        if payload.get("project_public_id"):
            proj = (await db.execute(
                select(Project).where(Project.public_id == payload["project_public_id"])
            )).scalar_one_or_none()
            project_id = proj.id if proj else None
            if project_id is None:
                raise _FKNotReady(f"finding {public_id}: project not present yet")
        if project_id is None:
            return
        host_id = None
        if payload.get("host_public_id"):
            host = (await db.execute(
                select(Host).where(Host.public_id == payload["host_public_id"])
            )).scalar_one_or_none()
            host_id = host.id if host else None
        item_id = None
        if payload.get("item_public_id"):
            item = (await db.execute(
                select(ChecklistItem).where(ChecklistItem.public_id == payload["item_public_id"])
            )).scalar_one_or_none()
            item_id = item.id if item else None
        execution_id = None
        if payload.get("execution_public_id"):
            ex = (await db.execute(
                select(Execution).where(Execution.public_id == payload["execution_public_id"])
            )).scalar_one_or_none()
            execution_id = ex.id if ex else None

        finding = (await db.execute(
            select(Finding).where(Finding.public_id == public_id)
        )).scalar_one_or_none()
        if finding is None:
            # Adopt on the same natural key the local ingester dedupes on
            # (project_id, item_id, host_id, title): another node may have found
            # the same thing with a different public_id.
            title = payload.get("title") or "Finding"
            nk = (await db.execute(
                select(Finding).where(
                    Finding.project_id == project_id,
                    Finding.item_id == item_id,
                    Finding.host_id == host_id,
                    Finding.title == title,
                )
            )).scalar_one_or_none()
            if nk is not None:
                nk.public_id = public_id
                finding = nk
            else:
                finding = Finding(
                    public_id=public_id, project_id=project_id,
                    item_id=item_id, execution_id=execution_id, title=title,
                )
                db.add(finding)

        finding.project_id = project_id
        finding.host_id = host_id or finding.host_id
        finding.item_id = item_id or finding.item_id
        finding.execution_id = execution_id or finding.execution_id
        finding.title = payload.get("title") or finding.title
        if "description" in payload:
            finding.description = payload["description"]
        try:
            finding.severity = FindingSeverity(payload.get("severity", "info"))
        except Exception:
            pass
        try:
            finding.status = FindingStatus(payload.get("status", "open"))
        except Exception:
            pass
        _apply_present(
            finding, payload,
            ("cvss_vector", "cvss_score", "cwe", "cve", "remediation", "notes",
             "references", "evidence_text"),
        )
        finding.source = payload.get("source") or finding.source or "alert"
        if payload.get("occurrences"):
            finding.occurrences = max(finding.occurrences or 0, int(payload["occurrences"]))
        finding.deleted_at = _parse_dt(payload.get("deleted_at"))
        finding.updated_at = _parse_dt(payload.get("updated_at")) or utcnow()


async def purge_old_events(db: AsyncSession, *, days: int) -> int:
    """Delete sync events older than `days`. Returns the deleted count.

    Only run when the operator opts in via SYNC_EVENT_RETENTION_DAYS: a peer
    offline longer than the window permanently loses the purged events.
    """
    cutoff = utcnow() - timedelta(days=days)
    result = await db.execute(delete(SyncEvent).where(SyncEvent.created_at < cutoff))
    return result.rowcount or 0


def _parse_dt(value: Any) -> Optional[datetime]:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            return None
    return None


sync_service = SyncService()
