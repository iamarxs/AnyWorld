"""FastAPI routes and an authenticated, socket-bound WebSocket gateway."""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from core.config import settings
from core.schemas import ClientPayload, ServerEvent
from logic.engine import GameEngine
from logic.llm_manager import LLMContextManager

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ConnectionManager:
    """Pending sockets are never broadcast subscribers.

    All dictionary operations are synchronous on the ASGI event loop. Promotion is
    invoked inside the engine authentication lock, without performing network I/O.
    """

    def __init__(self) -> None:
        """Track pending sockets and the active connection for each client."""
        self.active_connections: dict[str, WebSocket] = {}
        self.pending: set[WebSocket] = set()
        self._send_locks: dict[WebSocket, asyncio.Lock] = {}

    async def connect(self, client_id: str, websocket: WebSocket) -> bool:
        """Accept a pending socket, closing it if the pending cap is reached."""
        del client_id
        if len(self.pending) >= settings.server.max_pending_connections:
            await websocket.close(code=1013, reason="Too many pending connections")
            return False
        self.pending.add(websocket)
        self._send_locks[websocket] = asyncio.Lock()
        try:
            await websocket.accept()
        except BaseException:
            self.pending.discard(websocket)
            self._send_locks.pop(websocket, None)
            raise
        return True

    def promote(self, client_id: str, websocket: WebSocket) -> WebSocket | None:
        """Move an authenticated socket into the active map, returning any replaced socket."""
        if websocket not in self.pending:
            raise ValueError("Connection is no longer pending authentication.")
        previous = self.active_connections.get(client_id)
        self.pending.remove(websocket)
        self.active_connections[client_id] = websocket
        return previous

    def owns(self, client_id: str, websocket: WebSocket) -> bool:
        """Return whether the socket is the current active connection for the client."""
        return self.active_connections.get(client_id) is websocket

    async def disconnect(self, client_id: str, websocket: WebSocket) -> bool:
        """Remove a socket if it is the active connection for the client."""
        self.pending.discard(websocket)
        self._send_locks.pop(websocket, None)
        if not self.owns(client_id, websocket):
            return False
        del self.active_connections[client_id]
        return True

    async def broadcast_global(self, event: ServerEvent) -> None:
        """Send an event to all active connections."""
        await self._broadcast(event)

    async def broadcast_except(self, client_id: str, event: ServerEvent) -> None:
        """Send an event to all active connections except one client."""
        await self._broadcast(event, exclude=client_id)

    async def _broadcast(self, event: ServerEvent, exclude: str | None = None) -> None:
        """Serialize and send an event to all active connections, optionally excluding one."""
        message = event.model_dump_json()
        sockets = [socket for item, socket in self.active_connections.items() if item != exclude]
        await asyncio.gather(*(self._send_text(socket, message) for socket in sockets))

    async def send_personal(self, client_id: str, event: ServerEvent) -> None:
        """Send an event to a single client's active connection."""
        websocket = self.active_connections.get(client_id)
        if websocket is not None:
            await self.send_socket(websocket, event)

    async def send_socket(self, websocket: WebSocket, event: ServerEvent) -> None:
        """Send an event to a specific socket."""
        await self._send_text(websocket, event.model_dump_json())

    async def _send_text(self, websocket: WebSocket, message: str) -> None:
        """Send serialized text under the socket's send lock, closing on failure."""
        lock = self._send_locks.get(websocket)
        if lock is None:
            return
        try:
            async with asyncio.timeout(5):
                async with lock:
                    await websocket.send_text(message)
        except (TimeoutError, RuntimeError, OSError, WebSocketDisconnect):
            await self.close_socket(websocket)

    @staticmethod
    async def close_socket(websocket: WebSocket, code: int = 1000) -> None:
        """Close a socket, tolerating errors and timeouts."""
        try:
            async with asyncio.timeout(2):
                await websocket.close(code=code)
        except (TimeoutError, RuntimeError, OSError, WebSocketDisconnect):
            pass

    async def close(self) -> None:
        """Close all active and pending sockets and clear the connection maps."""
        sockets = set(self.active_connections.values()) | self.pending
        await asyncio.gather(*(self.close_socket(socket) for socket in sockets))
        self.active_connections.clear()
        self.pending.clear()
        self._send_locks.clear()


def create_app(resolver_factory=LLMContextManager) -> FastAPI:
    """Build the FastAPI app with its lifespan, routes and WebSocket gateway."""

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        """Validate passwords and manage engine/manager startup and shutdown."""
        settings.server.validate_passwords()
        manager = ConnectionManager()
        engine = GameEngine(manager, resolver_factory())
        application.state.manager = manager
        application.state.engine = engine
        try:
            yield
        finally:
            try:
                await engine.shutdown()
            finally:
                await manager.close()

    application = FastAPI(title="Anyworld", lifespan=lifespan)
    application.mount("/static", StaticFiles(directory=PROJECT_ROOT / "static"), name="static")
    templates = Jinja2Templates(directory=PROJECT_ROOT / "templates")

    @application.get("/", response_class=HTMLResponse)
    async def get_index(request: Request) -> HTMLResponse:
        """Serve the main HTML page."""
        return templates.TemplateResponse(request=request, name="index.html")

    @application.websocket("/ws/{client_id}")
    async def websocket_endpoint(websocket: WebSocket, client_id: str) -> None:
        """Handle a client socket: authenticate it and route its messages."""
        try:
            if str(UUID(client_id)) != client_id:
                raise ValueError("Noncanonical UUID")
        except (ValueError, AttributeError):
            await websocket.close(code=1008, reason="client_id must be a canonical UUID")
            return
        manager = websocket.app.state.manager
        engine = websocket.app.state.engine
        if not await manager.connect(client_id, websocket):
            return
        deadline = asyncio.get_running_loop().time() + settings.server.auth_timeout_seconds
        attempts = 0
        authenticated = False
        try:
            while True:
                try:
                    if not authenticated:
                        attempts += 1
                        async with asyncio.timeout_at(deadline):
                            raw_payload = await websocket.receive_json()
                    else:
                        raw_payload = await websocket.receive_json()
                    payload = ClientPayload.model_validate(raw_payload)
                    if not authenticated:
                        if payload.event_type != "auth":
                            raise ValueError("Authenticate before sending game messages.")
                        previous = []
                        await engine._authenticate(
                            client_id,
                            payload.data,
                            activate=lambda: previous.append(manager.promote(client_id, websocket)),
                        )
                        authenticated = True
                        if previous and previous[0] is not None:
                            await manager.close_socket(previous[0])
                    elif not manager.owns(client_id, websocket):
                        break
                    elif payload.event_type == "auth":
                        raise ValueError("This socket is already authenticated.")
                    else:
                        await engine.process_payload(
                            client_id,
                            payload,
                            authorize=lambda: manager.owns(client_id, websocket),
                        )
                except (ValidationError, ValueError) as exc:
                    message = (
                        "Invalid message schema." if isinstance(exc, ValidationError) else str(exc)
                    )
                    await manager.send_socket(
                        websocket, ServerEvent(type="error", payload={"msg": message})
                    )
                    if not authenticated and attempts >= settings.server.max_auth_attempts:
                        await manager.close_socket(websocket, code=1008)
                        break
        except TimeoutError:
            await manager.close_socket(websocket, code=1008)
        except (WebSocketDisconnect, RuntimeError):
            LOGGER.info("WebSocket disconnected")
        finally:
            # Hold the engine state lock across removal/marking to avoid a new
            # authenticated replacement being marked disconnected by an older socket.
            async with engine.lock:
                removed = await manager.disconnect(client_id, websocket)
                if removed:
                    player = engine.players.get(client_id)
                    version = player.connection_version if player is not None else None
                else:
                    version = None
            if removed:
                await engine.handle_disconnect(
                    client_id,
                    expected_version=version,
                    still_disconnected=lambda: client_id not in manager.active_connections,
                )

    return application


app = create_app()
