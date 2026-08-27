"""Authentication, scenario creation, chat, and lobby transitions."""

import hmac
import logging
from typing import TYPE_CHECKING

from core.config import settings
from core.schemas import ServerEvent
from logic.llm_manager import LLMResolutionError
from logic.models import GameState, Player

if TYPE_CHECKING:
    from logic.engine import GameEngine

LOGGER = logging.getLogger(__name__)


class LobbyMixin:
    """Lobby operations mixed into GameEngine to keep modules focused."""

    async def _authenticate(self: "GameEngine", client_id: str, data: dict[str, object]) -> None:
        name = self._clean_text(data.get("name"), "name", 40)
        password = self._clean_text(data.get("password"), "password", 200)
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
                if not hmac.compare_digest(password, expected):
                    error = "Invalid password for this session."
                else:
                    existing.is_connected = True
                    if self.state in {GameState.ACTIVE_TURN, GameState.AWAITING_LLM}:
                        existing.return_pending = True
                    name = existing.name
                    rejoined = True
            elif self.state is GameState.AWAITING_HOST:
                if not hmac.compare_digest(password, settings.server.host_password):
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
            elif not hmac.compare_digest(password, settings.server.player_password):
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
        await self.sender.broadcast_global(self._player_roster_event())

    async def _chat(self: "GameEngine", client_id: str, data: dict[str, object]) -> None:
        message = self._clean_text(data.get("message"), "message", 1_000)
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
        scenario = self._clean_text(data.get("scenario"), "scenario", 20_000)
        guidance = self._clean_optional_text(data.get("guidance"), "guidance", 5_000)
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
        try:
            resolution = await self.resolver.generate_initial_state()
        except LLMResolutionError as exc:
            async with self.lock:
                self.state = GameState.SCENARIO_INJECTION
            LOGGER.exception("Scenario generation failed")
            await self._send_error(client_id, str(exc))
            return

        async with self.lock:
            self.scenario_title = resolution.round_title or "Untitled Session"
            self.current_scenario_state = resolution.global_narrative
            self.state = GameState.AWAITING_PLAYERS
        initial_payload = resolution.model_dump()
        initial_payload["original_scenario"] = scenario
        await self.sender.broadcast_global(
            ServerEvent(type="state_update", payload=initial_payload)
        )
        await self.sender.send_personal(
            client_id,
            ServerEvent(type="scenario_ready", payload={"title": self.scenario_title}),
        )

    async def _start_game(self: "GameEngine", client_id: str, data: dict[str, object]) -> None:
        del data
        async with self.lock:
            player = self.players.get(client_id)
            if player is None or not player.is_host:
                error = "Only the host can start the game."
            elif self.state is not GameState.AWAITING_PLAYERS:
                error = "The game cannot be started in the current state."
            else:
                error = None
                self.state = GameState.ACTIVE_TURN
                directive = self._next_turn_locked()
                initial_state = self.current_scenario_state or ""
                title = self.scenario_title or "Untitled Session"
        if error is not None:
            await self._send_error(client_id, error)
            return

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
            ServerEvent(type="system_msg", payload={"msg": "The game has started."})
        )
        await self.sender.broadcast_global(
            ServerEvent(type="round_start", payload={"round_number": 1})
        )
        if directive is not None:
            await self.sender.broadcast_global(directive)

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
