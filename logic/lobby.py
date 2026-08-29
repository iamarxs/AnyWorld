"""Authentication, scenario creation, chat, and lobby transitions."""

import hashlib
import hmac
import logging
from typing import TYPE_CHECKING

from core.config import settings
from core.schemas import ClientPayload, ServerEvent
from logic.llm_manager import LLMResolutionError
from logic.models import GameState, Player
from logic.validation import clean_optional_text, clean_text

if TYPE_CHECKING:
    from logic.engine import GameEngine

LOGGER = logging.getLogger(__name__)


class LobbyMixin:
    """Lobby operations mixed into GameEngine to keep modules focused."""

    async def process_payload(self: "GameEngine", client_id: str, payload: ClientPayload) -> None:
        try:
            handler = getattr(self, self.PAYLOAD_HANDLERS[payload.event_type])
            await handler(client_id, payload.data)
        except ValueError as exc:
            await self._send_error(client_id, str(exc))

    async def _authenticate(self: "GameEngine", client_id: str, data: dict[str, object]) -> None:
        name = clean_text(data.get("name"), "name", 40)
        password = data.get("password")
        password_digest = data.get("password_digest")
        if isinstance(password, str):
            password_digest = hashlib.sha256(f"{password}{client_id}".encode()).hexdigest()
        else:
            password_digest = clean_text(password_digest, "password_digest", 64)
            if len(password_digest) != 64 or any(
                char not in "0123456789abcdef" for char in password_digest
            ):
                raise ValueError("'password_digest' must be a lowercase SHA-256 digest")
        error: str | None = None
        rejoined = False
        async with self.lock:
            existing = self.players.get(client_id)
            if existing is not None:
                expected = (
                    settings.server.host_password
                    if existing.is_host
                    else settings.server.player_password
                )
                if not self._password_matches(password_digest, expected, client_id):
                    error = "Invalid password for this session."
                else:
                    existing.is_connected = True
                    if self.state in {GameState.ACTIVE_TURN, GameState.AWAITING_LLM}:
                        existing.return_pending = True
                    name = existing.name
                    rejoined = True
            elif self.state is GameState.AWAITING_HOST:
                if not self._password_matches(
                    password_digest, settings.server.host_password, client_id
                ):
                    error = "The host must join first with the host password."
                else:
                    player = Player(client_id, name, True, join_index=len(self.join_order))
                    self.players[client_id] = player
                    self.join_order.append(client_id)
                    self.turn_queue.append(client_id)
                    self.state = GameState.SCENARIO_INJECTION
            elif self.state not in {
                GameState.SCENARIO_INJECTION,
                GameState.AWAITING_PLAYERS,
            }:
                error = "The game has already started."
            elif not self._password_matches(
                password_digest, settings.server.player_password, client_id
            ):
                error = "Invalid player password."
            elif self.state is GameState.SCENARIO_INJECTION:
                error = "The host is still creating the scenario."
            elif len(self.players) >= settings.server.max_players:
                error = "Server is at maximum capacity."
            elif any(player.name.casefold() == name.casefold() for player in self.players.values()):
                error = "That player name is already in use."
            else:
                player = Player(client_id, name, False, join_index=len(self.join_order))
                self.players[client_id] = player
                self.join_order.append(client_id)
                self.turn_queue.append(client_id)

            player = self.players.get(client_id)
            snapshot = self._snapshot_locked(player) if player is not None else None

        if error is not None:
            await self._send_error(client_id, error)
            return
        assert snapshot is not None
        await self.sender.send_personal(client_id, ServerEvent(type="auth_ok", payload=snapshot))
        verb = "rejoined" if rejoined else "connected"
        await self.sender.broadcast_global(
            ServerEvent(type="system_msg", payload={"msg": f"{name} {verb}."})
        )
        await self.sender.broadcast_except(client_id, self._player_roster_event())

    @staticmethod
    def _password_matches(digest: str, password: str, client_id: str) -> bool:
        expected = hashlib.sha256(f"{password}{client_id}".encode()).hexdigest()
        return hmac.compare_digest(digest, expected)

    async def _chat(self: "GameEngine", client_id: str, data: dict[str, object]) -> None:
        message = clean_text(data.get("message"), "message", 1_000)
        async with self.lock:
            player = self.players.get(client_id)
        if player is None or not player.is_connected:
            await self._send_error(client_id, "Authenticate before chatting.")
            return
        await self.sender.broadcast_global(
            ServerEvent(type="chat_echo", payload={"name": player.name, "chat": message})
        )

    async def _initialize_scenario(
        self: "GameEngine", client_id: str, data: dict[str, object]
    ) -> None:
        scenario = clean_text(data.get("scenario"), "scenario", 20_000)
        guidance = clean_optional_text(data.get("guidance"), "guidance", 5_000)
        async with self.lock:
            player = self.players.get(client_id)
            if player is None or not player.is_host:
                error = "Only the host can initialize the scenario."
            elif self.state is not GameState.SCENARIO_INJECTION:
                error = "The scenario cannot be changed in the current state."
            else:
                error = None
                self.state = GameState.AWAITING_LLM
        if error is not None:
            await self._send_error(client_id, error)
            return

        # Store only the scenario text for clients; DM guidance remains resolver-only.
        async with self.lock:
            self.original_scenario = scenario
        try:
            self.resolver.set_genesis(scenario, guidance)
        except TypeError as exc:
            # Preserve compatibility with lightweight resolver adapters that predate guidance.
            if "positional" not in str(exc) and "argument" not in str(exc):
                raise
            self.resolver.set_genesis(scenario)
        async with self.lock:
            self.scenario_title = "Untitled Session"
            self.current_scenario_state = ""
            self.state = GameState.AWAITING_PLAYERS
        await self.sender.send_personal(
            client_id,
            ServerEvent(type="scenario_ready", payload={"title": self.scenario_title}),
        )
        token_usage = getattr(self.resolver, "last_token_usage", None)
        if token_usage is not None:
            await self.sender.send_personal(
                client_id,
                ServerEvent(
                    type="token_usage",
                    payload={
                        "approximate_tokens": token_usage,
                        "context_window_size": getattr(self.resolver, "context_window_size", 8_192),
                    },
                ),
            )

    async def _start_game(self: "GameEngine", client_id: str, data: dict[str, object]) -> None:
        del data
        title = "Untitled Session"
        async with self.lock:
            player = self.players.get(client_id)
            if player is None or not player.is_host:
                error = "Only the host can start the game."
            elif self.state is not GameState.AWAITING_PLAYERS:
                error = "The game cannot be started in the current state."
            else:
                error = None
                self.state = GameState.AWAITING_LLM
                player_names = [self.players[player_id].name for player_id in self.join_order]
                title = self.scenario_title
        if error is not None:
            await self._send_error(client_id, error)
            return

        try:
            start_resolution = await self.resolver.generate_start_state(player_names)
        except (LLMResolutionError, AttributeError) as exc:
            LOGGER.exception("Player introduction generation failed")
            async with self.lock:
                self.state = GameState.AWAITING_PLAYERS
            await self._send_error(client_id, f"Could not start game: {exc}")
            return

        async with self.lock:
            self.scenario_title = start_resolution.round_title or "Untitled Session"
            self.current_scenario_state = start_resolution.global_narrative
            self.state = GameState.ACTIVE_TURN
            directive = self._next_turn_locked()
            initial_state = self.current_scenario_state

        try:
            await self.transcript.start(title, initial_state)
        except OSError as exc:
            LOGGER.exception("Could not create game transcript")
            async with self.lock:
                self.state = GameState.AWAITING_PLAYERS
                self.active_player_id = None
            await self._send_error(client_id, f"Could not create game transcript: {exc}")
            return
        await self.sender.broadcast_global(
            ServerEvent(type="state_update", payload=start_resolution.model_dump())
        )
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
            player = self.players.get(client_id)
            if player is None or not player.is_host:
                error = "Only the host can end the game."
            elif self.state not in {GameState.ACTIVE_TURN, GameState.AWAITING_LLM}:
                error = "The game cannot be ended in the current state."
            else:
                error = None
                self.state = GameState.ENDED
                self.active_player_id = None
        if error is not None:
            await self._send_error(client_id, error)
            return
        try:
            await self.transcript.finalize()
        except OSError:
            LOGGER.exception("Could not finalize game transcript")
        await self.sender.broadcast_global(
            ServerEvent(type="game_ended", payload={"msg": "The host ended the game."})
        )

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
