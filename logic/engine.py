"""Atomic game-state machine and sequential turn orchestrator."""

import asyncio
import logging
from collections import deque

from core.schemas import ServerEvent
from logic.dice import roll_d100
from logic.llm_manager import LLMResolutionError
from logic.lobby import LobbyMixin
from logic.models import EventSender, GameState, Player, ResolutionManager
from logic.presentation import name_resolution
from logic.transcript import GameTranscript
from logic.validation import clean_text

LOGGER = logging.getLogger(__name__)
IDLE_ACTION = "[SYSTEM INJECTION: Player disconnected. Idle.]"


class GameEngine(LobbyMixin):
    """Single-session DFA whose mutations are serialized by an asyncio lock."""

    PAYLOAD_HANDLERS = {
        "auth": "_authenticate",
        "chat": "_chat",
        "scenario_init": "_initialize_scenario",
        "start_game": "_start_game",
        "end_game": "_end_game",
        "action": "_submit_action",
    }

    def __init__(self, sender: EventSender, resolver: ResolutionManager) -> None:
        self.sender = sender
        self.resolver = resolver
        self.state = GameState.AWAITING_HOST
        self.players: dict[str, Player] = {}
        self.join_order: list[str] = []
        self.turn_queue: deque[str] = deque()
        self.round_buffer: dict[str, str] = {}
        self.active_player_id: str | None = None
        self.lock = asyncio.Lock()
        self.round_counter = 0
        self.scenario_title: str | None = None
        # Keep the host's public scenario separate from the private DM guidance.
        self.original_scenario: str | None = None
        self.current_scenario_state: str | None = None
        self.transcript = GameTranscript()

    async def handle_disconnect(self, client_id: str) -> None:
        actions: dict[str, str] | None = None
        directive: ServerEvent | None = None
        async with self.lock:
            player = self.players.get(client_id)
            if player is None or not player.is_connected:
                return
            player.is_connected = False
            player.departure_pending = True
            player.return_pending = False
            connected_players_remain = any(item.is_connected for item in self.players.values())
            if (
                self.state is GameState.ACTIVE_TURN
                and self.active_player_id == client_id
                and connected_players_remain
            ):
                self.round_buffer[client_id] = IDLE_ACTION
                self.turn_queue.rotate(-1)
                directive = self._next_turn_locked()
                actions = self._take_complete_round_locked()

        await self.sender.broadcast_global(
            ServerEvent(type="system_msg", payload={"msg": f"{player.name} disconnected."})
        )
        await self.sender.broadcast_global(self._player_roster_event())
        if directive is not None:
            await self.sender.broadcast_global(directive)
        if actions is not None:
            await self._resolve_round(actions)

    async def _submit_action(self, client_id: str, data: dict[str, object]) -> None:
        action = clean_text(data.get("action"), "action", 4_000)
        directive: ServerEvent | None = None
        action_event: ServerEvent | None = None
        actions: dict[str, str] | None = None
        async with self.lock:
            player = self.players.get(client_id)
            if player is None or not player.is_connected:
                error = "Authenticate before submitting an action."
            elif self.state is not GameState.ACTIVE_TURN:
                error = "Actions are blocked while no turn is active."
            elif client_id != self.active_player_id:
                error = "It is not your turn."
            else:
                error = None
                self.round_buffer[client_id] = action
                action_event = ServerEvent(
                    type="action_echo",
                    payload={
                        "round_number": self.round_counter + 1,
                        "player_name": player.name,
                        "player_color_index": player.join_index,
                        "action": action,
                    },
                )
                self.turn_queue.rotate(-1)
                directive = self._next_turn_locked()
                actions = self._take_complete_round_locked()
        if error is not None:
            await self._send_error(client_id, error)
            return
        if action_event is not None:
            await self.sender.broadcast_global(action_event)
        if directive is not None:
            await self.sender.broadcast_global(directive)
        if actions is not None:
            await self._resolve_round(actions)

    def _next_turn_locked(self) -> ServerEvent | None:
        if not any(player.is_connected for player in self.players.values()):
            self.active_player_id = None
            return None
        for _ in range(len(self.turn_queue)):
            client_id = self.turn_queue[0]
            player = self.players[client_id]
            if client_id in self.round_buffer:
                self.turn_queue.rotate(-1)
                continue
            if not player.is_connected:
                self.round_buffer[client_id] = IDLE_ACTION
                self.turn_queue.rotate(-1)
                continue
            self.active_player_id = client_id
            return ServerEvent(
                type="turn_directive",
                payload={
                    "active_player_id": client_id,
                    "active_player_name": player.name,
                    "round_number": self.round_counter + 1,
                    "submitted_actions": self._submitted_actions_locked(),
                    "player_order": [self.players[player_id].name for player_id in self.join_order],
                },
            )
        self.active_player_id = None
        return None

    def _submitted_actions_locked(self) -> dict[str, str]:
        return {
            self.players[client_id].name: action
            for client_id, action in self.round_buffer.items()
            if action != IDLE_ACTION
        }

    def _take_complete_round_locked(self) -> dict[str, str] | None:
        if not self.players or len(self.round_buffer) != len(self.players):
            return None
        self.state = GameState.AWAITING_LLM
        return dict(self.round_buffer)

    async def _resolve_round(self, actions: dict[str, str]) -> None:
        await self.sender.broadcast_global(
            ServerEvent(type="dm_thinking", payload={"active": True})
        )
        previous_state = self.current_scenario_state or ""
        async with self.lock:
            participant_data = {
                client_id: (
                    self.players[client_id].name,
                    self.players[client_id].departure_pending,
                    self.players[client_id].return_pending,
                )
                for client_id in actions
                if self.players[client_id].is_connected
                or self.players[client_id].departure_pending
                or self.players[client_id].return_pending
            }

        llm_actions: dict[str, str] = {}
        for client_id, (name, departed, returned) in participant_data.items():
            status_notes = []
            if departed:
                status_notes.append(
                    "[SYSTEM: Explain this player's in-world departure or inaction.]"
                )
            if returned:
                status_notes.append("[SYSTEM: Explain this player's in-world return.]")
            llm_actions[name] = " ".join([*status_notes, actions[client_id]])

        dice_results: dict[str, int] = {}
        hidden_rolls: set[str] = set()
        plan_dice = getattr(self.resolver, "plan_dice", None)
        if plan_dice is not None:
            try:
                dice_plan = await plan_dice(llm_actions)
                hidden_rolls = set(dice_plan.hidden_rolls)
                dice_results = {
                    name: roll_d100()
                    for name, required in dice_plan.rolls.items()
                    if required and name in llm_actions
                }
            except LLMResolutionError:
                LOGGER.exception("Dice planning failed; resolving without rolls")
        try:
            if plan_dice is None:
                resolution = await self.resolver.generate_resolution(llm_actions)
            else:
                resolution = await self.resolver.generate_resolution(llm_actions, dice_results)
        except LLMResolutionError as exc:
            LOGGER.exception("Round resolution failed")
            async with self.lock:
                self.round_buffer.clear()
                self.state = GameState.ACTIVE_TURN
                directive = self._next_turn_locked()
            await self.sender.broadcast_global(
                ServerEvent(type="dm_thinking", payload={"active": False})
            )
            await self.sender.broadcast_global(
                ServerEvent(type="error", payload={"msg": f"Round discarded: {exc}"})
            )
            if directive is not None:
                await self.sender.broadcast_global(directive)
            return

        async with self.lock:
            game_ended = self.state is GameState.ENDED
        if game_ended:
            await self.sender.broadcast_global(
                ServerEvent(type="dm_thinking", payload={"active": False})
            )
            return

        display_actions = {
            name: actions[client_id] for client_id, (name, _, _) in participant_data.items()
        }
        player_resolutions = {}
        for client_id, (name, _, _) in participant_data.items():
            result = resolution.player_resolutions.get(
                name,
                resolution.player_resolutions.get(client_id, "No resolution was provided."),
            )
            player_resolutions[name] = name_resolution(name, result)
        display_resolution = resolution.model_copy(
            update={"round_title": None, "player_resolutions": player_resolutions}
        )
        async with self.lock:
            self.round_counter += 1
            round_number = self.round_counter
            for client_id, (_, departed, returned) in participant_data.items():
                player = self.players[client_id]
                if departed:
                    player.departure_pending = False
                if returned:
                    player.return_pending = False
            self.current_scenario_state = resolution.global_narrative
            self.round_buffer.clear()
            self.state = GameState.ACTIVE_TURN
            directive = self._next_turn_locked()
        try:
            await self.transcript.append_round(
                round_number,
                previous_state,
                display_actions,
                display_resolution,
                {
                    name: value
                    for name, value in dice_results.items()
                    if name not in hidden_rolls
                },
            )
        except OSError:
            LOGGER.exception("Could not append round %s to transcript", round_number)
        state_payload = display_resolution.model_dump()
        state_payload["round_number"] = round_number
        state_payload["submitted_actions"] = {
            name: action for name, action in display_actions.items() if action != IDLE_ACTION
        }
        state_payload["player_order"] = [
            self.players[client_id].name for client_id in self.join_order
        ]
        state_payload["dice_results"] = {
            name: value for name, value in dice_results.items() if name not in hidden_rolls
        }
        await self.sender.broadcast_global(
            ServerEvent(type="dm_thinking", payload={"active": False})
        )
        await self.sender.broadcast_global(ServerEvent(type="state_update", payload=state_payload))
        host_id = next(
            (client_id for client_id in self.join_order if self.players[client_id].is_host), None
        )
        token_usage = getattr(self.resolver, "last_token_usage", None)
        if host_id is not None and token_usage is not None:
            await self.sender.send_personal(
                host_id,
                ServerEvent(
                    type="token_usage",
                    payload={
                        "approximate_tokens": token_usage,
                        "context_window_size": getattr(self.resolver, "context_window_size", 4_096),
                    },
                ),
            )
        if directive is not None:
            await self.sender.broadcast_global(
                ServerEvent(type="round_start", payload={"round_number": round_number + 1})
            )
            await self.sender.broadcast_global(directive)

    async def _send_error(self, client_id: str, message: str) -> None:
        await self.sender.send_personal(
            client_id, ServerEvent(type="error", payload={"msg": message})
        )
