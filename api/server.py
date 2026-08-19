"""FastAPI HTTP routes and WebSocket connection gateway."""

import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from core.schemas import ClientPayload, ServerEvent
from logic.engine import GameEngine
from logic.llm_manager import llm_manager

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ConnectionManager:
    """Own active sockets and provide resilient broadcast/unicast operations."""

    def __init__(self) -> None:
        self.active_connections: dict[str, WebSocket] = {}
        self._send_locks: dict[str, asyncio.Lock] = {}
        self._lock = asyncio.Lock()

    async def connect(self, client_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        async with self._lock:
            previous = self.active_connections.get(client_id)
            self.active_connections[client_id] = websocket
            self._send_locks.setdefault(client_id, asyncio.Lock())
        if previous is not None and previous is not websocket:
            try:
                await previous.close(code=1000, reason="Session reconnected")
            except RuntimeError:
                LOGGER.debug("Previous socket for %s was already closed", client_id)

    async def disconnect(self, client_id: str, websocket: WebSocket) -> bool:
        async with self._lock:
            if self.active_connections.get(client_id) is not websocket:
                return False
            del self.active_connections[client_id]
            self._send_locks.pop(client_id, None)
            return True

    async def broadcast_global(self, event: ServerEvent) -> None:
        async with self._lock:
            connections = list(self.active_connections.items())
        await asyncio.gather(
            *(self._send(client_id, websocket, event) for client_id, websocket in connections)
        )

    async def send_personal(self, client_id: str, event: ServerEvent) -> None:
        async with self._lock:
            websocket = self.active_connections.get(client_id)
        if websocket is not None:
            await self._send(client_id, websocket, event)

    async def _send(self, client_id: str, websocket: WebSocket, event: ServerEvent) -> None:
        async with self._lock:
            send_lock = self._send_locks.get(client_id)
        if send_lock is None:
            return
        try:
            async with send_lock:
                await websocket.send_text(event.model_dump_json())
        except (RuntimeError, OSError, WebSocketDisconnect):
            LOGGER.warning("Could not send %s event to %s", event.type, client_id)


manager = ConnectionManager()
game_engine = GameEngine(manager, llm_manager)
app = FastAPI(title="Artificial Dungeon")
app.mount(
    "/static",
    StaticFiles(directory=PROJECT_ROOT / "static"),
    name="static",
)
templates = Jinja2Templates(directory=PROJECT_ROOT / "templates")


@app.get("/", response_class=HTMLResponse)
async def get_index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request=request, name="index.html")


@app.websocket("/ws/{client_id}")
async def websocket_endpoint(websocket: WebSocket, client_id: str) -> None:
    await manager.connect(client_id, websocket)
    try:
        while True:
            try:
                raw_payload = await websocket.receive_json()
                payload = ClientPayload.model_validate(raw_payload)
            except ValidationError as exc:
                await manager.send_personal(
                    client_id,
                    ServerEvent(
                        type="error",
                        payload={
                            "msg": "Invalid message schema.",
                            "details": exc.errors(include_url=False, include_context=False),
                        },
                    ),
                )
                continue
            except ValueError:
                await manager.send_personal(
                    client_id,
                    ServerEvent(type="error", payload={"msg": "Message must be valid JSON."}),
                )
                continue
            await game_engine.process_payload(client_id, payload)
    except WebSocketDisconnect:
        LOGGER.info("WebSocket disconnected: %s", client_id)
    except RuntimeError:
        LOGGER.exception("WebSocket runtime failure for %s", client_id)
    finally:
        if await manager.disconnect(client_id, websocket):
            await game_engine.handle_disconnect(client_id)
