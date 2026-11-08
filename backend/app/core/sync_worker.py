"""Background worker for periodic peer pull/push synchronization."""
from __future__ import annotations

import logging

import asyncio
import time
from typing import Any

import aiohttp

from app.config import get_sync_peer_urls, settings
from app.core.sync import get_peer_urls_from_db, purge_old_events, sync_service
from app.database import async_session_maker

logger = logging.getLogger(__name__)

_IDLE_INTERVAL = 60
_DB_PEER_CHECK_INTERVAL = 60


def _capped_push_cursor(latest: int, min_deferred_ts: int | None) -> int:
    """Push cursor advance, capped below any event the receiver deferred.

    The receiver reports the smallest logical_ts it had to defer; advancing past
    it would strand that event (never re-pushed). Old receivers omit the field.
    """
    if min_deferred_ts is None:
        return latest
    return min(latest, min_deferred_ts - 1)


def _capped_push_seq(events: list, min_deferred_ts: int | None) -> int:
    """Push-cursor (local seq) advance after a batch, capped below deferred events.

    The receiver reports only the smallest logical_ts it deferred, so hold the cursor
    just below the first sent event (in seq order) at or after that ts. Re-pushing
    already-applied events in between is harmless (dedup by event_id).
    """
    latest = events[-1].id
    if min_deferred_ts is None:
        return latest
    held = [e.id for e in events if e.logical_ts >= min_deferred_ts]
    return min(latest, min(held) - 1) if held else latest


class SyncWorker:
    """Runs best-effort peer synchronization in background."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()
        self._session: aiohttp.ClientSession | None = None
        self._peer_fail_count: dict[str, int] = {}
        # Monotonic time before which a failing peer is skipped. A per-peer timestamp
        # (not a sleep inside the cycle) so one dead peer never delays the others or
        # a manual /sync/run.
        self._peer_next_attempt: dict[str, float] = {}
        self._max_backoff_seconds = 300
        self._last_db_peer_check: float = 0.0
        self._cached_db_peers: list[str] = []
        self._peers_dirty = True

    async def start(self) -> None:
        if not settings.SYNC_ENABLED:
            return
        if self._task is None or self._task.done():
            self._stopped.clear()
            self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._stopped.set()
        if self._task:
            self._task.cancel()
            try:
                await asyncio.wait_for(self._task, timeout=10)
            except (asyncio.CancelledError, asyncio.TimeoutError):
                pass
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    def invalidate_peer_cache(self) -> None:
        """Call when peers are added/removed via API to force a DB re-check."""
        self._peers_dirty = True

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        return self._session

    def _peer_headers(self) -> dict:
        token = settings.SYNC_PEER_TOKEN or ""
        return {"X-Sync-Token": token} if token else {}

    async def _resolve_peers(self) -> list[str]:
        """Build deduplicated peer list, caching DB lookups."""
        env_peers = get_sync_peer_urls()

        now = time.monotonic()
        need_db_check = (
            self._peers_dirty
            or (now - self._last_db_peer_check) >= _DB_PEER_CHECK_INTERVAL
        )

        if need_db_check:
            try:
                async with async_session_maker() as db:
                    self._cached_db_peers = await get_peer_urls_from_db(db)
                self._last_db_peer_check = now
                self._peers_dirty = False
            except Exception as exc:
                logger.error(f"[SyncWorker] Failed to load DB peers: {exc}")

        seen: set[str] = set()
        peers: list[str] = []
        for p in env_peers + self._cached_db_peers:
            u = (p or "").strip().rstrip("/")
            if u and u not in seen:
                seen.add(u)
                peers.append(u)
        return peers

    def _note_peer_failure(self, peer: str) -> float:
        """Record a failure, schedule the next attempt, return the backoff seconds."""
        fails = self._peer_fail_count.get(peer, 0) + 1
        self._peer_fail_count[peer] = fails
        backoff = min(
            settings.SYNC_PULL_INTERVAL_SECONDS * (2 ** (fails - 1)),
            self._max_backoff_seconds,
        )
        self._peer_next_attempt[peer] = time.monotonic() + backoff
        return backoff

    async def run_once(
        self, project_public_id: str | None = None, force: bool = False
    ) -> bool:
        """Run one full sync cycle. Returns True if peers were found.

        Peers still inside their failure backoff are skipped unless ``force`` (manual run).
        """
        if not settings.SYNC_ENABLED:
            return False
        peers = await self._resolve_peers()
        if not peers:
            return False
        for peer in peers:
            if not force and time.monotonic() < self._peer_next_attempt.get(peer, 0.0):
                continue
            try:
                await self._sync_peer(peer, project_public_id=project_public_id)
            except Exception as exc:
                backoff = self._note_peer_failure(peer)
                logger.error(
                    f"[SyncWorker] Peer sync failed for {peer}: {exc} (backoff {backoff}s)"
                )
        if settings.SYNC_EVENT_RETENTION_DAYS:
            try:
                async with async_session_maker() as db:
                    deleted = await purge_old_events(db, days=settings.SYNC_EVENT_RETENTION_DAYS)
                    await db.commit()
                    if deleted:
                        logger.info(f"[SyncWorker] Purged {deleted} old sync event(s)")
            except Exception as exc:
                logger.error(f"[SyncWorker] Retention purge failed: {exc}")
        return True

    async def _run_loop(self) -> None:
        while not self._stopped.is_set():
            try:
                had_peers = await self.run_once()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error(f"[SyncWorker] Cycle failed, continuing: {exc}")
                had_peers = False
            interval = (
                max(1, settings.SYNC_PULL_INTERVAL_SECONDS) if had_peers
                else _IDLE_INTERVAL
            )
            try:
                await asyncio.sleep(interval)
            except asyncio.CancelledError:
                break

    async def _sync_peer(self, peer_base_url: str, project_public_id: str | None = None) -> None:
        session = await self._ensure_session()
        sync_base = f"{peer_base_url}/api/v1/sync"

        # Cursor key: per-peer, and per-project for a project-scoped run so it can't
        # advance (and corrupt) the full-sync cursor. Keying on the URL (not NODE_ID)
        # also fixes the pull cursor never advancing and the shared-default-NODE_ID clash.
        cursor_key = peer_base_url + (f"::proj::{project_public_id}" if project_public_id else "")

        # 1) Pull remote events and ingest locally
        async with async_session_maker() as db:
            pull_cursor = await sync_service.get_cursor(db, cursor_key, "pull_seq")
        remote_node_id = None
        remote_max_seq = None
        # since_seq: the peer's own insertion order. logical_ts cursors lose events that
        # commit late or are relayed with an old timestamp.
        pull_url = f"{sync_base}/pull?since_seq={pull_cursor}&limit={settings.SYNC_BATCH_SIZE}"
        if project_public_id:
            pull_url += f"&project_public_id={project_public_id}"
        async with session.get(pull_url, headers=self._peer_headers()) as response:
            if response.status != 200:
                body = await response.text()
                backoff = self._note_peer_failure(peer_base_url)
                logger.error(
                    f"[SyncWorker] Pull from {peer_base_url} failed: status={response.status} "
                    f"body={body[:200]} (backoff {backoff}s)"
                )
                return
            else:
                self._peer_fail_count[peer_base_url] = 0
                self._peer_next_attempt.pop(peer_base_url, None)
                try:
                    payload = await response.json()
                except Exception as parse_err:
                    logger.warning(
                        f"[SyncWorker] Pull from {peer_base_url} returned non-JSON: {parse_err}"
                    )
                    events = []
                else:
                    events = payload.get("events", [])
                    remote_node_id = payload.get("node_id")
                    remote_max_seq = payload.get("max_seq")
        if isinstance(remote_max_seq, int) and pull_cursor > remote_max_seq:
            # The peer's log is behind our cursor (purged/recreated): its sequence
            # restarted. Re-pull from the start; ingest dedupes by event_id.
            logger.warning(
                f"[SyncWorker] {peer_base_url} max_seq={remote_max_seq} < cursor={pull_cursor}; resetting"
            )
            async with async_session_maker() as db:
                await sync_service.set_cursor(
                    db, peer_node_id=cursor_key, direction="pull_seq",
                    logical_ts=0, allow_decrease=True,
                )
                await db.commit()
            events = []
        # Ingest AFTER the response is drained and closed: no DB session spans HTTP.
        if events:
            async with async_session_maker() as db:
                await sync_service.ingest_remote_events(
                    db,
                    # lock on the peer's node id: the same key its pushes to us use
                    peer_node_id=remote_node_id or cursor_key,
                    # write the pull cursor under the SAME key we read it with
                    cursor_key=cursor_key,
                    events=events,
                )
                await db.commit()

        # 2) Push local events to peer
        async with async_session_maker() as db:
            push_cursor = await sync_service.get_cursor(db, cursor_key, "push_seq")
            local_events = await sync_service.pull_events(
                db,
                since_seq=push_cursor,
                limit=settings.SYNC_BATCH_SIZE,
                project_public_id=project_public_id,
            )
        if not local_events:
            return

        request_body: dict[str, Any] = {
            "source_node_id": settings.NODE_ID,
            "events": [
                {
                    "event_id": e.event_id,
                    "entity_type": e.entity_type,
                    "entity_public_id": e.entity_public_id,
                    "operation": e.operation,
                    "payload": e.payload,
                    "logical_ts": e.logical_ts,
                    "source_node_id": e.source_node_id,
                    "created_at": e.created_at.isoformat(),
                }
                for e in local_events
            ],
        }
        push_url = f"{sync_base}/push"
        # POST with no DB session open; cursor write gets a fresh session after.
        async with session.post(push_url, json=request_body, headers=self._peer_headers()) as response:
            push_status = response.status
            if push_status != 200:
                body = await response.text()
                logger.error(f"[SyncWorker] Push to {peer_base_url} failed: status={push_status} body={body[:200]}")
            else:
                try:
                    push_ack = await response.json()
                    min_deferred = push_ack.get("min_deferred_ts")
                except Exception:
                    min_deferred = None  # old receiver: no deferral report
                advance_to = _capped_push_seq(local_events, min_deferred)
                async with async_session_maker() as db:
                    await sync_service.set_cursor(
                        db,
                        peer_node_id=cursor_key,
                        direction="push_seq",
                        logical_ts=advance_to,
                    )
                    await db.commit()


sync_worker = SyncWorker()
