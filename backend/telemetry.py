import asyncio
import json
import logging
from typing import List, Dict, Any, Optional
from fastapi import WebSocket

logger = logging.getLogger(__name__)

class TelemetryManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []
        self._lock = asyncio.Lock()
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None
    def set_loop(self, loop: asyncio.AbstractEventLoop):
        self._main_loop = loop

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        # Capture running loop if not already set
        if not self._main_loop or not self._main_loop.is_running():
            try:
                self._main_loop = asyncio.get_running_loop()
            except RuntimeError:
                pass
        async with self._lock:
            self.active_connections.append(websocket)
        logger.info(f"WebSocket client connected. Total clients: {len(self.active_connections)}")
        # Send full multi-instance snapshot immediately
        try:
            from backend.instance_manager import instance_manager
            snapshot = await asyncio.get_running_loop().run_in_executor(None, instance_manager.snapshot)
        except Exception as e:
            logger.warning(f"state_sync snapshot failed: {e}")
            snapshot = []
        await self.send_personal_message({"type": "state_sync", "data": {"instances": snapshot}}, websocket)

    async def disconnect(self, websocket: WebSocket):
        async with self._lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
        logger.info(f"WebSocket client disconnected. Remaining clients: {len(self.active_connections)}")

    async def send_personal_message(self, message: Dict[str, Any], websocket: WebSocket):
        try:
            await websocket.send_text(json.dumps(message, ensure_ascii=False))
        except Exception as e:
            logger.warning(f"Error sending direct WebSocket message: {e}")

    async def broadcast(self, message: Dict[str, Any]):
        if not self.active_connections:
            return
        msg_str = json.dumps(message, ensure_ascii=False)
        disconnected = []
        async with self._lock:
            for connection in self.active_connections:
                try:
                    await connection.send_text(msg_str)
                except Exception as e:
                    logger.warning(f"Error broadcasting to client: {e}")
                    disconnected.append(connection)
            for d in disconnected:
                if d in self.active_connections:
                    self.active_connections.remove(d)

    def emit_sync(self, event_type: str, data: Any):
        """Helper to fire-and-forget broadcast from synchronous automation threads."""
        target_loop = self._main_loop
        if not target_loop or not target_loop.is_running():
            try:
                target_loop = asyncio.get_running_loop()
                self._main_loop = target_loop
            except RuntimeError:
                target_loop = None

        if target_loop and target_loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(
                    self.broadcast({"type": event_type, "data": data}),
                    target_loop
                )
            except Exception as e:
                logger.debug(f"emit_sync failed to schedule: {e}")

telemetry_manager = TelemetryManager()
