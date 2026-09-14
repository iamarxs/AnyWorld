"""Authentication, scenario creation, chat, and lobby transitions."""

import hashlib
import hmac
import logging
from collections.abc import Callable
from contextvars import ContextVar
from typing import TYPE_CHECKING

from core.config import settings
from core.schemas import ClientPayload, ServerEvent
from logic.models import GameState, Player
from logic.validation import clean_optional_text, clean_text

if TYPE_CHECKING:
    from logic.engine import GameEngine

LOGGER = logging.getLogger(__name__)
CURRENT_OWNER: ContextVar[Callable[[], bool]] = ContextVar("socket_owner", default=lambda: True)


class LobbyMixin:
    """Lobby operations; authentication can atomically activate a transport connection."""

    async def process_payload(
        self: "GameEngine",
        client_id: str,
        payload: ClientPayload,
        *,
        authorize: Callable[[], bool] = lambda: True,
    ) -> None:
        token = CURRENT_OWNER.set(authorize)
        try:
            if not authorize():
                return
            handler = getattr(self, self.PAYLOAD_HANDLERS[payload.event_type])
            await handler(client_id, payload.data)
        except ValueError as exc:
            await self._send_error(client_id, str(exc))
            if payload.event_type == "action":
                async with self.effects_lock:
                    async with self.lock:
                        directive = (
                            self._next_turn_locked()
                            if authorize() and self.active_player_id == client_id
                            else None
                        )
                    if directive is not None:
                        await self.sender.send_personal(client_id, directive)
        finally:
            CURRENT_OWNER.reset(token)

    async def _authenticate(
        self: "GameEngine",
        client_id: str,
        data: dict[str, object],
        *,
        activate: Callable[[], None] | None = None,
    ) -> bool:
        name = clean_text(data.get("name"), "name", 40)
        digest = clean_text(data.get("password_digest"), "password_digest", 64)
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise ValueError("'password_digest' must be a lowercase SHA-256 digest")
        directive = None
        actions = None
        async with self.lock:
            existing = self.players.get(client_id)
            if existing is not None:
                expected = (
                    settings.server.host_password
                    if existing.is_host
                    else (settings.server.player_password)
                )
                if not self._password_matches(digest, expected, client_id):
                    raise ValueError("Invalid password for this session.")
                reconnect_token = data.get("reconnect_token")
                if not isinstance(reconnect_token, str) or not hmac.compare_digest(
                    reconnect_token, existing.reconnect_token
                ):
                    raise ValueError("Invalid reconnect token for this session.")
            elif self.state is GameState.AWAITING_HOST:
                if not self._password_matches(digest, settings.server.host_password, client_id):
                    raise ValueError("The host must join first with the host password.")
            else:
                if self.state is not GameState.AWAITING_PLAYERS:
                    raise ValueError("The game is not accepting new players.")
                if not self._password_matches(digest, settings.server.player_password, client_id):
                    raise ValueError("Invalid player password.")
                if len(self.players) >= settings.server.max_players:
                    raise ValueError("Server is at maximum capacity.")
                if any(p.name.casefold() == name.casefold() for p in self.players.values()):
                    raise ValueError("That player name is already in use.")

            # Synchronous callback: socket promotion and domain authentication share the
            # same critical section. Invalid credentials never replace the old socket.
            if activate is not None:
                activate()
            rejoined = existing is not None
            if existing is not None:
                if not existing.is_connected:
                    existing.connection_version += 1
                    if self.state in {GameState.ACTIVE_TURN, GameState.AWAITING_LLM}:
                        existing.return_pending = True
                existing.is_connected = True
                name = existing.name
                player = existing
            else:
                player = Player(
                    client_id,
                    name,
                    self.state is GameState.AWAITING_HOST,
                    join_index=len(self.join_order),
                )
                self.players[client_id] = player
                self.join_order.append(client_id)
                self.turn_queue.append(client_id)
                if player.is_host:
                    self.state = GameState.SCENARIO_INJECTION
            if self.state is GameState.ACTIVE_TURN and (
                self.active_player_id is None
                or not self.players[self.active_player_id].is_connected
            ):
                directive = self._next_turn_locked()
                actions = self._take_complete_round_locked()
                if actions is not None:
                    self._launch_round_locked(actions)
            snapshot = self._snapshot_locked(player)
        await self.sender.send_personal(client_id, ServerEvent(type="auth_ok", payload=snapshot))
        verb = "rejoined" if rejoined else "connected"
        await self.sender.broadcast_global(
            ServerEvent(type="system_msg", payload={"msg": f"{name} {verb}."})
        )
        await self.sender.broadcast_except(client_id, self._player_roster_event())
        if directive is not None:
            await self.sender.broadcast_global(directive)
        return True

    @staticmethod
    def _password_matches(digest: str, password: str | None, client_id: str) -> bool:
        if not password:
            return False
        expected = hashlib.sha256(f"{password}{client_id}".encode()).hexdigest()
        return hmac.compare_digest(digest, expected)

    async def _chat(self: "GameEngine", client_id: str, data: dict[str, object]) -> None:
        message = clean_text(data.get("message"), "message", 1_000)
        async with self.lock:
            if not CURRENT_OWNER.get()():
                return
            player = self.players.get(client_id)
            if player is None or not player.is_connected:
                raise ValueError("Authenticate before chatting.")
        await self.sender.broadcast_global(
            ServerEvent(type="chat_echo", payload={"name": player.name, "chat": message})
        )

    async def _initialize_scenario(
        self: "GameEngine", client_id: str, data: dict[str, object]
    ) -> None:
        scenario = clean_text(data.get("scenario"), "scenario", 20_000)
        guidance = clean_optional_text(data.get("guidance"), "guidance", 5_000)
        async with self.lock:
            if not CURRENT_OWNER.get()():
                return
            player = self.players.get(client_id)
            if player is None or not player.is_host:
                raise ValueError("Only the host can initialize the scenario.")
            if self.state is not GameState.SCENARIO_INJECTION:
                raise ValueError("The scenario cannot be changed in the current state.")
            self._launch_job_locked(
                lambda epoch: self._prepare_scenario(epoch, client_id, scenario, guidance),
                GameState.SCENARIO_INJECTION,
            )

    async def _prepare_scenario(
        self: "GameEngine", epoch: int, client_id: str, scenario: str, guidance: str
    ) -> None:
        self.resolver.set_genesis(scenario, guidance)
        resolution = await self.resolver.generate_initial_state()
        async with self.effects_lock:
            async with self.lock:
                if not self._job_current(epoch):
                    return
                self.original_scenario = scenario
                self.scenario_title = resolution.round_title or "Untitled Session"
                self.current_scenario_state = resolution.global_narrative
                self.state = GameState.AWAITING_PLAYERS
            await self.sender.send_personal(
                client_id,
                ServerEvent(type="scenario_ready", payload={"title": self.scenario_title}),
            )
            await self._publish_usage(client_id)

    async def _start_game(self: "GameEngine", client_id: str, data: dict[str, object]) -> None:
        del data
        async with self.lock:
            if not CURRENT_OWNER.get()():
                return
            player = self.players.get(client_id)
            if player is None or not player.is_host:
                raise ValueError("Only the host can start the game.")
            if self.state is not GameState.AWAITING_PLAYERS:
                raise ValueError("The game cannot be started in the current state.")
            names = [self.players[item].name for item in self.join_order]
            self._launch_job_locked(
                lambda epoch: self._prepare_start(epoch, names), GameState.AWAITING_PLAYERS
            )

    async def _prepare_start(self: "GameEngine", epoch: int, names: list[str]) -> None:
        resolution = await self.resolver.generate_start_state(names)
        async with self.effects_lock:
            async with self.lock:
                if not self._job_current(epoch):
                    return
            await self.transcript.start(
                self.scenario_title or "Untitled Session", resolution.global_narrative
            )
            async with self.lock:
                if not self._job_current(epoch):
                    return
                self.current_scenario_state = resolution.global_narrative
                self.state = GameState.ACTIVE_TURN
                directive = self._next_turn_locked()
            payload = resolution.model_dump()
            payload.update(
                round_title=self.scenario_title, original_scenario=self.original_scenario
            )
            await self.sender.broadcast_global(ServerEvent(type="state_update", payload=payload))
            await self.sender.broadcast_global(
                ServerEvent(type="system_msg", payload={"msg": "The game has started."})
            )
            await self.sender.broadcast_global(
                ServerEvent(type="round_start", payload={"round_number": 1})
            )
            if directive is not None:
                await self.sender.broadcast_global(directive)

    async def _end_game(self: "GameEngine", client_id: str, data: dict[str, object]) -> None:
        del data
        async with self.lock:
            if not CURRENT_OWNER.get()():
                return
            player = self.players.get(client_id)
            if player is None or not player.is_host:
                raise ValueError("Only the host can end the game.")
            if self.state not in {GameState.ACTIVE_TURN, GameState.AWAITING_LLM}:
                raise ValueError("The game cannot be ended in the current state.")
        await self.shutdown(close_resolver=False)

    def _player_roster_event(self: "GameEngine") -> ServerEvent:
        return ServerEvent(
            type="player_roster",
            payload={
                "players": [
                    {
                        "name": self.players[player_id].name,
                        "connected": self.players[player_id].is_connected,
                        "is_host": self.players[player_id].is_host,
                    }
                    for player_id in self.join_order
                ]
            },
        )

    def _snapshot_locked(self: "GameEngine", player: Player) -> dict[str, object]:
        return {
            "client_id": player.client_id,
            "reconnect_token": player.reconnect_token,
            "round_paused": self.round_paused,
            "name": player.name,
            "is_host": player.is_host,
            "state": self.state.name,
            "scenario_title": self.scenario_title,
            "original_scenario": self.original_scenario,
            "scenario_state": self.current_scenario_state,
            "completed_round_number": self.round_counter or None,
            "active_player_id": self.active_player_id,
            "active_player_name": (
                self.players[self.active_player_id].name
                if self.active_player_id is not None
                else None
            ),
            "round_number": (
                self.round_counter + 1
                if self.state in {GameState.ACTIVE_TURN, GameState.AWAITING_LLM}
                else None
            ),
            "submitted_actions": self._submitted_actions_locked(),
            "player_order": [self.players[client_id].name for client_id in self.join_order],
            "players": [
                {
                    "name": self.players[client_id].name,
                    "connected": self.players[client_id].is_connected,
                    "is_host": self.players[client_id].is_host,
                }
                for client_id in self.join_order
            ],
        }
