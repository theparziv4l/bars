"""Background worker for periodic peer pull/push synchronization."""
from __future__ import annotations

import asyncio
import time
from typing import Any

import aiohttp

from app.config import get_sync_peer_urls, settings
from app.core.sync import get_peer_urls_from_db, sync_service
from app.database import async_session_maker

_IDLE_INTERVAL = 60
_DB_PEER_CHECK_INTERVAL = 60


class SyncWorker:
    """Runs best-effort peer synchronization in background."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stopped = asyncio.Event()
        self._session: aiohttp.ClientSession | None = None
        self._peer_fail_count: dict[str, int] = {}
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
            await self._task
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
            except Exception as exc:
                print(f"[SyncWorker] Failed to load DB peers: {exc}")
            self._last_db_peer_check = now
            self._peers_dirty = False

        seen: set[str] = set()
        peers: list[str] = []
        for p in env_peers + self._cached_db_peers:
            u = (p or "").strip().rstrip("/")
            if u and u not in seen:
                seen.add(u)
                peers.append(u)
        return peers

    async def run_once(self, project_public_id: str | None = None) -> bool:
        """Run one full sync cycle. Returns True if peers were found."""
        if not settings.SYNC_ENABLED:
            return False
        peers = await self._resolve_peers()
        if not peers:
            return False
        for peer in peers:
            try:
                await self._sync_peer(peer, project_public_id=project_public_id)
            except Exception as exc:
                print(f"[SyncWorker] Peer sync failed for {peer}: {exc}")
        return True

    async def _run_loop(self) -> None:
        while not self._stopped.is_set():
            had_peers = await self.run_once()
            interval = (
                max(1, settings.SYNC_PULL_INTERVAL_SECONDS) if had_peers
                else _IDLE_INTERVAL
            )
            await asyncio.sleep(interval)

    async def _sync_peer(self, peer_base_url: str, project_public_id: str | None = None) -> None:
        session = await self._ensure_session()
        sync_base = f"{peer_base_url}/api/v1/sync"

        # Cursor key: per-peer, and per-project for a project-scoped run so it can't
        # advance (and corrupt) the full-sync cursor. Keying on the URL (not NODE_ID)
        # also fixes the pull cursor never advancing and the shared-default-NODE_ID clash.
        cursor_key = peer_base_url + (f"::proj::{project_public_id}" if project_public_id else "")

        # 1) Pull remote events and ingest locally
        async with async_session_maker() as db:
            pull_cursor = await sync_service.get_cursor(db, cursor_key, "pull")
        pull_url = f"{sync_base}/pull?since_logical_ts={pull_cursor}&limit={settings.SYNC_BATCH_SIZE}"
        if project_public_id:
            pull_url += f"&project_public_id={project_public_id}"
        async with session.get(pull_url) as response:
            if response.status != 200:
                body = await response.text()
                self._peer_fail_count[peer_base_url] = self._peer_fail_count.get(peer_base_url, 0) + 1
                backoff = min(
                    settings.SYNC_PULL_INTERVAL_SECONDS * (2 ** (self._peer_fail_count[peer_base_url] - 1)),
                    self._max_backoff_seconds,
                )
                print(
                    f"[SyncWorker] Pull from {peer_base_url} failed: status={response.status} "
                    f"body={body[:200]} (backoff {backoff}s)"
                )
                await asyncio.sleep(backoff)
                return
            else:
                self._peer_fail_count[peer_base_url] = 0
                try:
                    payload = await response.json()
                except Exception as parse_err:
                    print(
                        f"[SyncWorker] Pull from {peer_base_url} returned non-JSON: {parse_err}"
                    )
                else:
                    events = payload.get("events", [])
                    source_node_id = payload.get("node_id", peer_base_url)
                    if events:
                        async with async_session_maker() as db:
                            await sync_service.ingest_remote_events(
                                db,
                                # write the pull cursor under the SAME key we read it with
                                peer_node_id=cursor_key,
                                events=events,
                            )
                            await db.commit()

        # 2) Push local events to peer
        async with async_session_maker() as db:
            push_cursor = await sync_service.get_cursor(db, cursor_key, "push")
            local_events = await sync_service.pull_events(
                db,
                since_logical_ts=push_cursor,
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
            async with session.post(push_url, json=request_body) as response:
                if response.status != 200:
                    body = await response.text()
                    print(f"[SyncWorker] Push to {peer_base_url} failed: status={response.status} body={body[:200]}")
                elif response.status == 200:
                    latest = local_events[-1].logical_ts
                    async with async_session_maker() as db:
                        await sync_service.set_cursor(
                            db,
                            peer_node_id=cursor_key,
                            direction="push",
                            logical_ts=latest,
                        )
                        await db.commit()


sync_worker = SyncWorker()
