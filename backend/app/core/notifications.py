"""Notification manager for pattern-based alerts."""
import asyncio
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Set
from dataclasses import dataclass, field
from enum import Enum


class AlertSeverity(str, Enum):
    """Alert severity levels."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class Alert:
    """Alert triggered by pattern match."""
    id: str
    execution_id: int
    severity: AlertSeverity
    pattern: str
    matched_text: str
    context_line: Optional[str]
    timestamp: datetime = field(default_factory=datetime.utcnow)
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
    
    def __init__(self):
        """Initialize notification manager."""
        self._alerts: Dict[str, Alert] = {}
        self._listeners: Set[Callable] = set()
        self._alert_counter = 0
        self._lock = asyncio.Lock()
    
    def _generate_alert_id(self) -> str:
        """Generate unique alert ID."""
        self._alert_counter += 1
        return f"alert_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{self._alert_counter}"
    
    async def add_listener(self, callback: Callable) -> None:
        """Add a notification listener."""
        async with self._lock:
            self._listeners.add(callback)
    
    async def remove_listener(self, callback: Callable) -> None:
        """Remove a notification listener."""
        async with self._lock:
            self._listeners.discard(callback)
    
    async def notify(self, event_type: str, data: Any) -> None:
        """Send notification to all listeners."""
        async with self._lock:
            listeners = list(self._listeners)
        
        for listener in listeners:
            try:
                if asyncio.iscoroutinefunction(listener):
                    await listener(event_type, data)
                else:
                    listener(event_type, data)
            except Exception as e:
                # Log but don't crash on listener errors
                print(f"Notification listener error: {e}")
    
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
            severity = AlertSeverity(detection["severity"])
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
            if alert_id in self._alerts:
                self._alerts[alert_id].acknowledged = True
                await self.notify("alert_acknowledged", {"id": alert_id})
                return True
        return False
    
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
            "timestamp": datetime.utcnow().isoformat()
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
            "timestamp": datetime.utcnow().isoformat()
        })


# Singleton instance
notification_manager = NotificationManager()
