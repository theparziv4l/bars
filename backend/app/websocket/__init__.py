"""WebSocket handlers for real-time updates."""
from typing import Dict, Set
from fastapi import WebSocket, WebSocketDisconnect
import asyncio
import json

from app.core.notifications import notification_manager
from app.core.pubsub import pubsub_backend
from app.core.auth import decode_access_token
from app.config import settings


class ConnectionManager:
    """Manages WebSocket connections."""
    
    def __init__(self):
        self.active_connections: Dict[str, Set[WebSocket]] = {}
        self._lock = asyncio.Lock()
    
    async def connect(
        self, websocket: WebSocket, channel: str = "default", accept: bool = True
    ):
        """Accept and register a WebSocket connection. Set accept=False when subscribing to additional channels."""
        if accept:
            await websocket.accept()
        async with self._lock:
            if channel not in self.active_connections:
                self.active_connections[channel] = set()
            self.active_connections[channel].add(websocket)
    
    async def disconnect(self, websocket: WebSocket, channel: str = "default"):
        """Remove a WebSocket connection (prune empty channels)."""
        async with self._lock:
            if channel in self.active_connections:
                self.active_connections[channel].discard(websocket)
                if not self.active_connections[channel]:
                    del self.active_connections[channel]

    async def broadcast(self, message: dict, channel: str = "default", timeout: float = 5.0):
        """Broadcast message to all connections in a channel.

        Sends run concurrently with a per-send timeout: one stalled client
        can neither delay nor block the rest; stalled/failed sends disconnect.
        """
        async with self._lock:
            connections = list(self.active_connections.get(channel, []))

        async def _send(websocket: WebSocket) -> None:
            try:
                await asyncio.wait_for(websocket.send_json(message), timeout)
            except Exception:
                await self.disconnect(websocket, channel)

        if connections:
            await asyncio.gather(*(_send(ws) for ws in connections))
    
    async def send_to_execution(self, execution_id: int, message: dict):
        """Send message to execution-specific channel."""
        await self.broadcast(message, f"execution:{execution_id}")
    
    async def send_to_project(self, project_id: int, message: dict):
        """Send message to project-specific channel."""
        await self.broadcast(message, f"project:{project_id}")


manager = ConnectionManager()


async def notification_listener(event_type: str, data: dict):
    """Listen for notifications and broadcast to WebSocket clients."""
    message = {"type": event_type, "data": data}
    
    await pubsub_backend.publish("notifications", message)

    sent_ws: set[int] = set()

    async def _broadcast_dedup(channel: str) -> None:
        async with manager._lock:
            connections = list(manager.active_connections.get(channel, []))
        targets = []
        for ws in connections:
            ws_id = id(ws)
            if ws_id in sent_ws:
                continue
            sent_ws.add(ws_id)
            targets.append(ws)

        async def _send(ws) -> None:
            try:
                await asyncio.wait_for(ws.send_json(message), 5.0)
            except Exception:
                await manager.disconnect(ws, channel)

        if targets:
            await asyncio.gather(*(_send(ws) for ws in targets))

    if event_type == "output" and "execution_id" in data:
        await _broadcast_dedup(f"execution:{data['execution_id']}")
    elif event_type in ("alert", "execution_status"):
        await _broadcast_dedup("default")
        if "execution_id" in data:
            await _broadcast_dedup(f"execution:{data['execution_id']}")
    elif event_type in ("flow_completed", "flow_failed"):
        await _broadcast_dedup("default")
    elif event_type in ("item_claimed", "item_released", "item_taken_over"):
        await _broadcast_dedup("default")
        if "project_id" in data:
            await _broadcast_dedup(f"project:{data['project_id']}")
    else:
        await _broadcast_dedup("default")


# Register notification listener
async def setup_websocket_listener():
    """Set up the notification listener."""
    await notification_manager.add_listener(notification_listener)


async def _member_of(db, project_id: int, user_id: int) -> bool:
    """Direct membership check (no HTTP semantics in WS code)."""
    from sqlalchemy import select
    from app.models.user import ProjectMember
    row = await db.execute(
        select(ProjectMember.id).where(
            ProjectMember.project_id == project_id,
            ProjectMember.user_id == user_id,
            ProjectMember.deleted_at.is_(None),
        )
    )
    return row.scalar_one_or_none() is not None


async def _channel_allowed(channel: str, user) -> bool:
    """Whether an identified user may join a channel."""
    if channel == "default":
        return True
    from sqlalchemy import select
    from app.database import async_session_maker
    async with async_session_maker() as db:
        if channel.startswith("execution:"):
            try:
                eid = int(channel.split(":", 1)[1])
            except ValueError:
                return False
            from app.models.checklist import ChecklistGroup, ChecklistItem
            from app.models.execution import Execution
            pid = (await db.execute(
                select(ChecklistGroup.project_id)
                .join(ChecklistItem, ChecklistItem.group_id == ChecklistGroup.id)
                .join(Execution, Execution.item_id == ChecklistItem.id)
                .where(Execution.id == eid)
            )).scalar_one_or_none()
            if pid is None:
                return False
            return await _member_of(db, pid, user.id)
        elif channel.startswith("project:"):
            try:
                pid = int(channel.split(":", 1)[1])
            except ValueError:
                return False
            return await _member_of(db, pid, user.id)
        return False


async def handle_websocket(websocket: WebSocket, channel: str = "default"):
    """Handle a WebSocket connection."""
    user = None
    if settings.WS_REQUIRE_AUTH:
        token = websocket.headers.get("Authorization")
        if token and token.lower().startswith("bearer "):
            token = token[7:].strip()
        if not token:
            token = websocket.query_params.get("token")
        if not token:
            await websocket.close(code=4401)
            return
        try:
            payload = decode_access_token(token)
        except Exception:
            await websocket.close(code=4401)
            return
        from sqlalchemy import select
        from app.database import async_session_maker
        from app.models.user import User
        async with async_session_maker() as db:
            user = (await db.execute(
                select(User).where(User.public_id == payload.get("sub"),
                                  User.is_active.is_(True))
            )).scalar_one_or_none()
        if user is None:
            await websocket.close(code=4401)
            return
        # The initial channel gets the same check as subscribe (no bypass).
        if not await _channel_allowed(channel, user):
            await websocket.close(code=4403)
            return

    await manager.connect(websocket, channel)
    subscribed_channels: set[str] = {channel}

    try:
        while True:
            # Keep connection alive and handle incoming messages
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30)
                try:
                    message = json.loads(data)
                except (json.JSONDecodeError, TypeError):
                    await websocket.send_json({"type": "error", "detail": "invalid message"})
                    continue

                # Handle different message types
                if message.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
                elif message.get("type") == "subscribe":
                    new_channel = message.get("channel", "default")
                    if user is not None and not await _channel_allowed(new_channel, user):
                        await websocket.send_json({"type": "error", "detail": "channel access denied"})
                        continue
                    await manager.connect(websocket, new_channel, accept=False)
                    subscribed_channels.add(new_channel)
                elif message.get("type") == "unsubscribe":
                    old_channel = message.get("channel", "default")
                    await manager.disconnect(websocket, old_channel)
                    subscribed_channels.discard(old_channel)

            except asyncio.TimeoutError:
                # Send heartbeat
                await websocket.send_json({"type": "heartbeat"})

    except WebSocketDisconnect:
        pass
    finally:
        for ch in list(subscribed_channels):
            try:
                await manager.disconnect(websocket, ch)
            except Exception:
                pass
