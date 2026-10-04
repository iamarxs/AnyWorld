"""FastAPI routes and an authenticated, socket-bound WebSocket gateway."""

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from core.config import settings
from core.schemas import ClientPayload, ServerEvent, validate_client_data
from api.admission import WindowBudget, receive_payload, origin_allowed, source_address
from api.windows_asyncio import install_windows_socket_cleanup
from logic.engine import GameEngine
from logic.llm_manager import LLMContextManager
from logic.models import GameState, Player

LOGGER = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class ConnectionManager:
    """Pending sockets are private; each socket owns a bounded ordered writer."""

    def __init__(self) -> None:
        self.active_connections: dict[str, WebSocket] = {}
        self.pending: set[WebSocket] = set()
        self._queues: dict[WebSocket, asyncio.Queue] = {}
        self._writers: dict[WebSocket, asyncio.Task] = {}
        self._bytes: dict[WebSocket, int] = {}
        self._ids: dict[WebSocket, str] = {}
        self._cleanup_tasks: set[asyncio.Task] = set()
        self._retired_writers: set[asyncio.Task] = set()
        self.on_disconnect = None

    async def connect(self, client_id: str, websocket: WebSocket) -> bool:
        if len(self.pending) >= settings.server.max_pending_connections:
            await websocket.close(code=1013, reason="Too many pending connections")
            return False
        self.pending.add(websocket)
        self._ids[websocket] = client_id
        self._queues[websocket] = asyncio.Queue(settings.server.send_queue_messages)
        self._bytes[websocket] = 0
        try:
            await websocket.accept()
        except BaseException:
            self._forget(websocket)
            raise
        self._writers[websocket] = asyncio.create_task(self._write(websocket))
        return True

    def promote(self, client_id: str, websocket: WebSocket) -> WebSocket | None:
        if websocket not in self.pending:
            raise ValueError("Connection is no longer pending authentication.")
        previous = self.active_connections.get(client_id)
        self.pending.remove(websocket)
        self.active_connections[client_id] = websocket
        return previous

    def owns(self, client_id: str, websocket: WebSocket) -> bool:
        return self.active_connections.get(client_id) is websocket

    def _forget(self, websocket: WebSocket) -> bool:
        self.pending.discard(websocket)
        client_id = self._ids.pop(websocket, None)
        removed = client_id is not None and self.owns(client_id, websocket)
        if removed:
            del self.active_connections[client_id]
        self._queues.pop(websocket, None)
        self._bytes.pop(websocket, None)
        writer = self._writers.pop(websocket, None)
        if writer is not None:
            self._retired_writers.add(writer)
            writer.add_done_callback(self._retired_writers.discard)
            if writer is not asyncio.current_task():
                writer.cancel()
        return removed

    async def disconnect(self, client_id: str, websocket: WebSocket) -> bool:
        return self._forget(websocket)

    async def broadcast_global(self, event: ServerEvent) -> None:
        await self._broadcast(event)

    async def broadcast_except(self, client_id: str, event: ServerEvent) -> None:
        await self._broadcast(event, client_id)

    async def _broadcast(self, event: ServerEvent, exclude: str | None = None) -> None:
        message = event.model_dump_json()
        for item, socket in list(self.active_connections.items()):
            if item != exclude:
                self._enqueue(socket, message)
        await asyncio.sleep(0)

    async def send_personal(self, client_id: str, event: ServerEvent) -> None:
        socket = self.active_connections.get(client_id)
        if socket is not None:
            await self.send_socket(socket, event)

    async def send_socket(self, websocket: WebSocket, event: ServerEvent) -> None:
        self._enqueue(websocket, event.model_dump_json())
        await asyncio.sleep(0)

    def _enqueue(self, websocket: WebSocket, message: str) -> None:
        queue = self._queues.get(websocket)
        if queue is None:
            return
        size = len(message.encode("utf-8"))
        if queue.full() or self._bytes[websocket] + size > settings.server.send_queue_bytes:
            self._fail(websocket)
            return
        self._bytes[websocket] += size
        queue.put_nowait((message, size))

    async def _write(self, websocket: WebSocket) -> None:
        queue = self._queues[websocket]
        try:
            while True:
                message, size = await queue.get()
                try:
                    async with asyncio.timeout(5):
                        await websocket.send_text(message)
                finally:
                    if websocket in self._bytes:
                        self._bytes[websocket] -= size
                    queue.task_done()
        except (TimeoutError, RuntimeError, OSError, WebSocketDisconnect):
            self._fail(websocket)

    def _fail(self, websocket: WebSocket) -> None:
        client_id = self._ids.get(websocket)
        removed = self._forget(websocket)

        async def clean():
            if removed and self.on_disconnect is not None:
                await self.on_disconnect(client_id)
            await self.close_socket(websocket, code=1013)

        task = asyncio.create_task(clean())
        self._cleanup_tasks.add(task)
        task.add_done_callback(self._cleanup_tasks.discard)

    @staticmethod
    async def close_socket(websocket: WebSocket, code: int = 1000) -> None:
        try:
            async with asyncio.timeout(2):
                await websocket.close(code=code)
        except (TimeoutError, RuntimeError, OSError, WebSocketDisconnect):
            pass

    async def close(self) -> None:
        sockets = set(self._queues)
        writers = [*self._writers.values(), *self._retired_writers]
        for socket in sockets:
            self._forget(socket)
        if writers:
            await asyncio.gather(*writers, return_exceptions=True)
        await asyncio.gather(*(self.close_socket(socket) for socket in sockets))
        cleanups = list(self._cleanup_tasks)
        for task in cleanups:
            task.cancel()
        if cleanups:
            await asyncio.gather(*cleanups, return_exceptions=True)


def create_app(resolver_factory=LLMContextManager) -> FastAPI:
    """Build the FastAPI app with its lifespan, routes and WebSocket gateway."""

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        """Validate passwords and manage engine/manager startup and shutdown."""
        settings.server.validate_passwords()
        install_windows_socket_cleanup()
        manager = ConnectionManager()
        engine = GameEngine(manager, resolver_factory())

        async def lost(client_id):
            await application.state.engine.handle_disconnect(
                client_id, still_disconnected=lambda: client_id not in manager.active_connections
            )

        manager.on_disconnect = lost
        application.state.source_failures = WindowBudget(
            settings.server.failed_logins_per_source, 60
        )
        application.state.global_failures = WindowBudget(
            settings.server.failed_logins_global, 60, 1
        )
        application.state.player_messages = WindowBudget(
            settings.server.player_messages_per_window,
            settings.server.player_message_window_seconds,
        )
        application.state.history_messages = WindowBudget(30, 10)
        application.state.manager = manager
        application.state.engine = engine
        try:
            yield
        finally:
            try:
                await application.state.engine.shutdown()
            finally:
                await manager.close()

    application = FastAPI(title="Anyworld", lifespan=lifespan)
    application.mount("/static", StaticFiles(directory=PROJECT_ROOT / "static"), name="static")

    @application.get("/", response_class=FileResponse)
    async def get_index() -> FileResponse:
        """Serve the main HTML page."""
        return FileResponse(PROJECT_ROOT / "templates" / "index.html")

    async def start_new_game(websocket: WebSocket, client_id: str) -> GameEngine:
        """Replace an ended session, retaining only its authenticated host."""
        manager = websocket.app.state.manager
        previous = websocket.app.state.engine
        async with previous.lock:
            host = previous.players.get(client_id)
            if not manager.owns(client_id, websocket) or host is None or not host.is_host:
                raise ValueError("Only the host can start a new game.")
            if previous.state is not GameState.ENDED:
                raise ValueError("End the current game before starting a new one.")

        # Construction touches archive directories; keep it outside the state lock.
        # No awaits until publication: no socket can join or restart between these steps.
        engine = GameEngine(manager, resolver_factory())
        host = Player(client_id, host.name, True, reconnect_token=host.reconnect_token)
        engine.players[client_id] = host
        engine.join_order.append(client_id)
        engine.turn_queue.append(client_id)
        engine.state = GameState.SCENARIO_INJECTION
        sockets = [
            socket for owner, socket in manager.active_connections.items() if owner != client_id
        ]
        for socket in sockets:
            manager._forget(socket)
        websocket.app.state.engine = engine
        await asyncio.gather(*(manager.close_socket(socket, code=4002) for socket in sockets))
        await previous.shutdown()
        await manager.send_personal(
            client_id, ServerEvent(type="auth_ok", payload=engine._snapshot_locked(host))
        )
        LOGGER.info("New game created; previous players disconnected")
        return engine

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
        source = source_address(websocket)
        source_failures = websocket.app.state.source_failures
        global_failures = websocket.app.state.global_failures
        if not origin_allowed(websocket):
            await websocket.close(code=1008, reason="Origin is not allowed")
            return
        if source_failures.blocked(source) or global_failures.blocked("global"):
            await websocket.close(code=1013, reason="Authentication temporarily limited")
            return
        if not await manager.connect(client_id, websocket):
            return
        deadline = asyncio.get_running_loop().time() + settings.server.auth_timeout_seconds
        attempts = 0
        authenticated = False
        try:
            while True:
                raw_payload = None
                try:
                    if not authenticated:
                        attempts += 1
                        async with asyncio.timeout_at(deadline):
                            raw_payload = await receive_payload(websocket)
                    else:
                        raw_payload = await receive_payload(websocket)
                        if not manager.owns(client_id, websocket):
                            break
                        # History has its own bounded budget so catch-up/export cannot
                        # consume the player's chat/action allowance.
                        history = (
                            isinstance(raw_payload, dict)
                            and raw_payload.get("event_type") == "journal_request"
                        )
                        budget = (
                            websocket.app.state.history_messages
                            if history
                            else websocket.app.state.player_messages
                        )
                        if not budget.accept(client_id):
                            error = {"msg": "Message rate exceeded; please wait."}
                            if history:
                                error.update(
                                    code="journal_rate_limited",
                                    retry_after_seconds=budget.retry_after(client_id),
                                    request=raw_payload.get("data", {}),
                                )
                            await manager.send_socket(
                                websocket, ServerEvent(type="error", payload=error)
                            )
                            continue
                    payload = ClientPayload.model_validate(raw_payload)
                    validate_client_data(payload)
                    engine = websocket.app.state.engine
                    if not authenticated:
                        if payload.event_type != "auth":
                            raise ValueError("Authenticate before sending game messages.")
                        if source_failures.blocked(source) or global_failures.blocked("global"):
                            await manager.close_socket(websocket, code=1013)
                            break
                        previous = []

                        def activate():
                            if engine is not websocket.app.state.engine:
                                raise ValueError("The game changed. Please join again.")
                            previous.append(manager.promote(client_id, websocket))

                        await engine._authenticate(
                            client_id,
                            payload.data,
                            activate=activate,
                        )
                        authenticated = True
                        if previous and previous[0] is not None:
                            await manager.close_socket(previous[0], code=4001)
                    elif not manager.owns(client_id, websocket):
                        break
                    elif payload.event_type == "auth":
                        raise ValueError("This socket is already authenticated.")
                    elif payload.event_type == "new_game":
                        engine = await start_new_game(websocket, client_id)
                    else:
                        await engine.process_payload(
                            client_id,
                            payload,
                            authorize=lambda: engine is websocket.app.state.engine
                            and manager.owns(client_id, websocket),
                        )
                except (ValidationError, ValueError) as exc:
                    if not authenticated:
                        source_failures.record(source)
                        global_failures.record("global")
                    message = (
                        "Invalid message schema." if isinstance(exc, ValidationError) else str(exc)
                    )
                    error = {"msg": message}
                    if (
                        authenticated
                        and isinstance(raw_payload, dict)
                        and raw_payload.get("event_type") == "journal_request"
                    ):
                        error.update(
                            code="journal_request_failed", request=raw_payload.get("data", {})
                        )
                    await manager.send_socket(websocket, ServerEvent(type="error", payload=error))
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
