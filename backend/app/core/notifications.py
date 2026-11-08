"""Notification manager for pattern-based alerts."""
import logging
import asyncio
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set
from dataclasses import dataclass, field
from enum import Enum
from sqlalchemy import event
from sqlalchemy.orm import Session

from app.core.utils import utcnow

logger = logging.getLogger(__name__)

_AFTER_COMMIT = "_notify_after_commit"
_after_commit_tasks: Set[asyncio.Task] = set()


def notify_after_commit(db, coro_fn: Callable, *args, **kwargs) -> None:
    """Run ``await coro_fn(*args, **kwargs)`` once *db*'s outer transaction commits.

    Awaiting websocket fan-out inside an open write transaction holds SQLite's single
    write lock for up to 5 s per listener, and listeners that look the row up in their
    own session can't see it yet. Queue the call instead; it is dropped on rollback.
    ponytail: a savepoint rollback does not drop calls queued inside it.
    """
    sess = getattr(db, "sync_session", db)
    sess.info.setdefault(_AFTER_COMMIT, []).append((coro_fn, args, kwargs))


async def _run_deferred(coro_fn, args, kwargs) -> None:
    try:
        await coro_fn(*args, **kwargs)
    except Exception as e:
        logger.error(f"Deferred notification failed: {e}")


@event.listens_for(Session, "after_commit")
def _flush_after_commit(session) -> None:
    if session.in_nested_transaction():
        return  # SQLAlchemy fires this on SAVEPOINT release too; wait for the outer commit
    pending = session.info.pop(_AFTER_COMMIT, None)
    if not pending:
        return
    loop = asyncio.get_running_loop()
    for coro_fn, args, kwargs in pending:
        task = loop.create_task(_run_deferred(coro_fn, args, kwargs))
        _after_commit_tasks.add(task)
        task.add_done_callback(_after_commit_tasks.discard)


@event.listens_for(Session, "after_rollback")
def _drop_after_rollback(session) -> None:
    session.info.pop(_AFTER_COMMIT, None)


class AlertSeverity(str, Enum):
    """Alert severity levels."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


_SEVERITY_ALIASES = {
    "critical": AlertSeverity.CRITICAL, "high": AlertSeverity.CRITICAL,
    "medium": AlertSeverity.WARNING, "warning": AlertSeverity.WARNING,
    "warn": AlertSeverity.WARNING,
    "low": AlertSeverity.INFO, "info": AlertSeverity.INFO,
}
_unknown_severities_logged: Set[str] = set()


def coerce_alert_severity(value: Any) -> AlertSeverity:
    """Map user-defined alert_patterns keys (high/medium/low/warn/...) to an
    AlertSeverity, case-insensitively. Unknown keys become WARNING (logged once)."""
    key = str(value or "").strip().lower()
    sev = _SEVERITY_ALIASES.get(key)
    if sev is None:
        if key not in _unknown_severities_logged:
            _unknown_severities_logged.add(key)
            logger.warning(f"Unknown alert severity {value!r}; treating as warning")
        return AlertSeverity.WARNING
    return sev


@dataclass
class Alert:
    """Alert triggered by pattern match."""
    id: str
    execution_id: int
    severity: AlertSeverity
    pattern: str
    matched_text: str
    context_line: Optional[str]
    timestamp: datetime = field(default_factory=utcnow)
    acknowledged: bool = False
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "id": self.id,
            "execution_id": self.execution_id,
            "severity": self.severity.value,
            "pattern": self.pattern,
            "matched_text": self.matched_text,
            "context_line": self.context_line,
            "timestamp": self.timestamp.isoformat(),
            "acknowledged": self.acknowledged
        }


class NotificationManager:
    """
    Manager for real-time notifications and alerts.
    
    Features:
    - Pattern-based alert triggering
    - Severity levels (info, warning, critical)
    - WebSocket notification broadcasting
    - Alert history tracking
    """

    MAX_ALERTS = 1000

    def __init__(self):
        """Initialize notification manager."""
        self._alerts: Dict[str, Alert] = {}
        self._listeners: Set[Callable] = set()
        self._alert_counter = 0
        self._lock = asyncio.Lock()
    
    def _generate_alert_id(self) -> str:
        """Generate unique alert ID."""
        self._alert_counter += 1
        return f"alert_{utcnow().strftime('%Y%m%d%H%M%S')}_{self._alert_counter}"
    
    async def add_listener(self, callback: Callable) -> None:
        """Add a notification listener."""
        async with self._lock:
            self._listeners.add(callback)
    
    async def remove_listener(self, callback: Callable) -> None:
        """Remove a notification listener."""
        async with self._lock:
            self._listeners.discard(callback)
    
    async def notify(self, event_type: str, data: Any) -> None:
        """Send notification to all listeners (concurrently, errors isolated)."""
        async with self._lock:
            listeners = list(self._listeners)

        async def _invoke(listener: Callable) -> None:
            try:
                if asyncio.iscoroutinefunction(listener):
                    await listener(event_type, data)
                else:
                    listener(event_type, data)
            except Exception as e:
                # Log but don't crash on listener errors
                logger.error(f"Notification listener error: {e}")

        if listeners:
            await asyncio.gather(*(_invoke(li) for li in listeners))
    
    async def create_alert(
        self,
        execution_id: int,
        severity: AlertSeverity,
        pattern: str,
        matched_text: str,
        context_line: Optional[str] = None
    ) -> Alert:
        """Create and broadcast a new alert."""
        async with self._lock:
            alert_id = self._generate_alert_id()
            
            alert = Alert(
                id=alert_id,
                execution_id=execution_id,
                severity=severity,
                pattern=pattern,
                matched_text=matched_text,
                context_line=context_line
            )
            
            self._alerts[alert_id] = alert
            while len(self._alerts) > self.MAX_ALERTS:
                # FIFO eviction: dicts preserve insertion order.
                self._alerts.pop(next(iter(self._alerts)))

        # Broadcast alert
        await self.notify("alert", alert.to_dict())
        
        return alert
    
    async def process_output_chunk(
        self,
        execution_id: int,
        output_chunk: str,
        alert_patterns: Dict[str, List[str]]
    ) -> List[Alert]:
        """
        Process output chunk for alert patterns.
        
        Args:
            execution_id: ID of the execution
            output_chunk: New output text
            alert_patterns: Dict of severity -> patterns
        
        Returns:
            List of triggered alerts
        """
        from app.core.parser import output_parser
        
        triggered_alerts = []
        detected = output_parser.detect_alerts(output_chunk, alert_patterns)
        
        for detection in detected:
            severity = coerce_alert_severity(detection["severity"])
            alert = await self.create_alert(
                execution_id=execution_id,
                severity=severity,
                pattern=detection["pattern"],
                matched_text=detection["matched_text"],
                context_line=detection.get("line")
            )
            triggered_alerts.append(alert)
        
        return triggered_alerts
    
    async def acknowledge_alert(self, alert_id: str) -> bool:
        """Mark an alert as acknowledged."""
        async with self._lock:
            if alert_id not in self._alerts:
                return False
            self._alerts[alert_id].acknowledged = True
            execution_id = self._alerts[alert_id].execution_id
        # Notify OUTSIDE the lock: notify re-acquires it (non-reentrant).
        # execution_id lets the WS layer scope delivery by project membership.
        await self.notify("alert_acknowledged", {"id": alert_id, "execution_id": execution_id})
        return True
    
    async def get_alerts(
        self,
        execution_id: Optional[int] = None,
        severity: Optional[AlertSeverity] = None,
        acknowledged: Optional[bool] = None,
        limit: int = 100
    ) -> List[Alert]:
        """Get alerts with optional filtering."""
        async with self._lock:
            alerts = list(self._alerts.values())
        
        # Apply filters
        if execution_id is not None:
            alerts = [a for a in alerts if a.execution_id == execution_id]
        if severity is not None:
            alerts = [a for a in alerts if a.severity == severity]
        if acknowledged is not None:
            alerts = [a for a in alerts if a.acknowledged == acknowledged]
        
        # Sort by timestamp (newest first) and limit
        alerts.sort(key=lambda a: a.timestamp, reverse=True)
        return alerts[:limit]
    
    async def clear_alerts(self, execution_id: Optional[int] = None) -> int:
        """Clear alerts, optionally for a specific execution."""
        async with self._lock:
            if execution_id is None:
                count = len(self._alerts)
                self._alerts.clear()
            else:
                to_remove = [
                    aid for aid, a in self._alerts.items()
                    if a.execution_id == execution_id
                ]
                for aid in to_remove:
                    del self._alerts[aid]
                count = len(to_remove)
        
        return count
    
    async def send_execution_status(
        self,
        execution_id: int,
        status: str,
        details: Optional[Dict[str, Any]] = None,
        item_id: Optional[int] = None
    ) -> None:
        """Send execution status update."""
        await self.notify("execution_status", {
            "execution_id": execution_id,
            "status": status,
            "item_id": item_id,
            "details": details or {},
            "timestamp": utcnow().isoformat()
        })

    async def send_project_update(
        self,
        type: str, # 'host_created', 'variable_updated', etc.
        project_id: int,
        data: Dict[str, Any]
    ) -> None:
        """Send project-level update."""
        await self.notify(type, {
            "project_id": project_id,
            "data": data,
            "timestamp": utcnow().isoformat()
        })


# Singleton instance
notification_manager = NotificationManager()
