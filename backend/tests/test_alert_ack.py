"""Acknowledge never deadlocks; alert store is capped."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.notifications import AlertSeverity, NotificationManager


async def _run():
    mgr = NotificationManager()
    seen = []

    async def _listener(event_type, data):
        seen.append((event_type, data))

    await mgr.add_listener(_listener)
    alert = await mgr.create_alert(execution_id=1, severity=AlertSeverity.INFO,
                                   pattern="p", matched_text="m")
    ok = await asyncio.wait_for(mgr.acknowledge_alert(alert.id), timeout=5.0)
    assert ok is True
    assert any(e == "alert_acknowledged" for e, _ in seen), seen
    for i in range(1005):
        await mgr.create_alert(execution_id=1, severity=AlertSeverity.INFO,
                               pattern="p", matched_text=str(i))
    assert len(mgr._alerts) == 1000, len(mgr._alerts)
    assert alert.id not in mgr._alerts  # oldest evicted first


def test_alert_ack_and_cap():
    asyncio.run(_run())


if __name__ == "__main__":
    test_alert_ack_and_cap()
