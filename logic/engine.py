"""Atomic game state with owned, cancellable inference tasks."""

import asyncio
import json
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from uuid import uuid4

from core.config import settings
from core.schemas import RoundResolution, ServerEvent
from logic.dice import roll_chance, roll_d100, validate_chance_events
from logic.llm.errors import LLMBackendUnavailableError, LLMResolutionError
from logic.lobby import CURRENT_OWNER, LobbyMixin
from logic.models import (
    EventSender,
    GameState,
    Player,
    ResolutionManager,
    Participant,
    PendingRound,
    RoundActions,
)
from logic.transcript import GameTranscript
from logic.journal import PublicJournal
from logic.validation import clean_text

LOGGER = logging.getLogger(__name__)
IDLE_ACTION = "[SYSTEM INJECTION: Player disconnected. Idle.]"
USAGE_REFRESH_TIMEOUT_SECONDS = 5.0


class GameEngine(LobbyMixin):
    """Coordinate one game session without holding state locks across I/O.

    ``lock`` protects mutable game state.  ``effects_lock`` orders committed
    broadcasts and transcript writes against shutdown.  Inference tasks carry a
    generation number so a cancelled or stale job cannot revive an ended session.
    """

    PAYLOAD_HANDLERS = {
        "chat": "_chat",
        "scenario_init": "_initialize_scenario",
        "start_game": "_start_game",
        "end_game": "_end_game",
        "action": "_submit_action",
        "retry_round": "_retry_round",
        "journal_request": "_journal_page",
    }

    def __init__(self, sender: EventSender, resolver: ResolutionManager) -> None:
        """Initialize the engine with its event sender and resolution backend."""
        self.sender, self.resolver = sender, resolver
        self.state = GameState.AWAITING_HOST
        self.players: dict[str, Player] = {}
        self.join_order: list[str] = []
        self.turn_queue: deque[str] = deque()
        self.round_buffer: dict[str, str] = {}
        self.active_player_id: str | None = None
        self.lock = asyncio.Lock()
        # Order committed transcript/events against end-game, without holding the
        # state lock or serializing chat behind model inference.
        self.effects_lock = asyncio.Lock()
        self.generation = 0
        self.inference_task: asyncio.Task | None = None
        self.round_paused = False
        # A paused round keeps its original actions and authoritative dice/events so
        # a host retry can rerun narrative validation without rerolling anything.
        self.pending_resolution: PendingRound | None = None
        self.round_counter = 0
        self.scenario_title: str | None = None
        self.original_scenario: str | None = None
        self.private_guidance = ""
        self.freeform_guidance = ""
        self.chance_event = ""
        self.chance_rule = None
        self.current_scenario_state: str | None = None
        self.opening_scenario: str | None = None
        self.transcript = GameTranscript()
        self.session_id = str(uuid4())
        self.journal = PublicJournal(self.session_id)
        self.latest_round: dict[str, object] | None = None
        self.accepted_actions: dict[tuple[str, str], dict[str, object]] = {}
        self.pending_delivery: deque[ServerEvent] = deque()
        self.publication_lock = asyncio.Lock()
        self._published_cursor = 0

    async def _deliver_pending(self) -> None:
        """Finish committed events in order, under effects_lock, without inference."""
        while self.pending_delivery:
            await self._broadcast(self.pending_delivery[0])
            self.pending_delivery.popleft()

    async def _broadcast(self, event: ServerEvent) -> None:
        # Preserve journal/broadcast order, including cancellation and concurrent chat.
        # This lock never spans inference or transcripts.
        async with self.publication_lock:
            try:
                event = await self.journal.record(event)
                await self._publish_recorded(event)
            except asyncio.CancelledError:
                if event.payload.get("session_id") == self.session_id and event.payload.get(
                    "event_id"
                ):
                    # Recorded events retain their identity after cancellation.
                    # Finish delivery before later IDs pass it.
                    await self._publish_recorded(event)
                raise

    async def _publish_recorded(self, event: ServerEvent) -> None:
        event_id = event.payload.get("event_id")
        if isinstance(event_id, int) and event_id <= self._published_cursor:
            return
        if event.type == "state_update" and event.payload.get("round_number"):
            self.latest_round = dict(event.payload)
        await self.sender.broadcast_global(event)
        if isinstance(event_id, int):
            self._published_cursor = event_id

    async def _journal_page(self, client_id: str, data: dict[str, object]) -> None:
        from core.schemas import JournalInput

        request = JournalInput.model_validate(data)
        page = await self.journal.page(request.after, request.limit, request.search)
        page["mode"] = request.mode
        page["search"] = request.search
        await self.sender.send_personal(client_id, ServerEvent(type="journal_page", payload=page))

    def _job_current(self, epoch: int) -> bool:
        """Return whether the job's epoch is current and the session has not ended."""
        return self.generation == epoch and self.state is not GameState.ENDED

    def _launch_job_locked(
        self, work: Callable[[int], Awaitable[None]], failure_state: GameState
    ) -> None:
        """Start an owned inference task for the given work and failure state."""
        self.generation += 1
        self.state = GameState.AWAITING_LLM
        self.round_paused = False
        self.inference_task = asyncio.create_task(
            self._run_job(work, self.generation, failure_state)
        )

    async def _run_job(
        self, work: Callable[[int], Awaitable[None]], epoch: int, failure_state: GameState
    ) -> None:
        """Run an owned inference job, handling failures and cleanup."""
        LOGGER.info("Inference job started generation=%d phase=%s", epoch, failure_state.name)
        try:
            async with self.effects_lock:
                if not self._job_current(epoch):
                    return
                await self._broadcast(ServerEvent(type="dm_thinking", payload={"active": True}))
            async with asyncio.timeout(settings.llm.request_timeout_seconds * 3):
                for attempt in range(3):
                    try:
                        await work(epoch)
                        break
                    except LLMResolutionError:
                        if (
                            attempt == 2
                            or failure_state is not GameState.AWAITING_LLM
                            or not self._job_current(epoch)
                            or self.pending_resolution is None
                        ):
                            raise
                        LOGGER.info(
                            "Automatically retrying round=%d generation=%d retry=%d/2",
                            self.round_counter + 1,
                            epoch,
                            attempt + 1,
                        )
            LOGGER.info(
                "Inference job finished generation=%d current=%s state=%s",
                epoch,
                self._job_current(epoch),
                self.state.name,
            )
        except asyncio.CancelledError:
            LOGGER.info("Inference job cancelled generation=%d", epoch)
            raise
        except Exception as exc:
            # Boundary for an owned task: consume failures so the session never
            # remains spinning forever. Do not expose provider bodies/private guidance.
            LOGGER.warning("Inference job failed generation=%d error=%s", epoch, type(exc).__name__)
            connection_hint = (
                "Could not connect to the LLM backend. Check that the model server is running "
                "and its configured endpoint is reachable before retrying. "
                if isinstance(exc, LLMBackendUnavailableError)
                else ""
            )
            async with self.effects_lock:
                async with self.lock:
                    if not self._job_current(epoch):
                        return
                    committed = self.state is not GameState.AWAITING_LLM
                    if committed:
                        directive = self._next_turn_locked()
                    else:
                        self.state = failure_state
                        self.round_paused = failure_state is GameState.AWAITING_LLM
                if committed:
                    # Delivery can hit the job deadline after state has committed.
                    # Do not offer a retry for actions that have already resolved.
                    LOGGER.warning("Preserving committed state generation=%d", epoch)
                    had_pending = bool(self.pending_delivery)
                    await self._deliver_pending()
                    if not had_pending and directive is not None:
                        await self._broadcast(directive)
                    return
                await self._broadcast(
                    ServerEvent(
                        type="error",
                        payload={
                            "msg": (
                                "Round paused; actions and dice are retained. "
                                "The host can retry or end."
                                if self.round_paused
                                else "Could not prepare the game. Please try again."
                            ),
                            "round_paused": self.round_paused,
                            "state": self.state.name,
                        },
                    )
                )
                for player in self.players.values():
                    if player.is_host and player.is_connected:
                        await self.sender.send_personal(
                            player.client_id,
                            ServerEvent(
                                type="error",
                                payload={
                                    "msg": connection_hint
                                    + "Inference could not complete; see host diagnosis.",
                                    "diagnosis": str(exc)[:1000],
                                    "phase": failure_state.name,
                                    "round_paused": self.round_paused,
                                    "state": self.state.name,
                                },
                            ),
                        )
        finally:
            try:
                async with self.effects_lock:
                    if self._job_current(epoch):
                        await self._broadcast(
                            ServerEvent(type="dm_thinking", payload={"active": False})
                        )
                if self._job_current(epoch):
                    await self._refresh_usage()
                async with self.effects_lock:
                    if self._job_current(epoch):
                        await self._publish_usage()
            finally:
                if self.inference_task is asyncio.current_task():
                    self.inference_task = None

    async def wait_for_inference(self) -> None:
        """Join owned work (used by shutdown callers and deterministic tests)."""
        task = self.inference_task
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)

    async def shutdown(self, *, close_resolver: bool = True) -> None:
        """Terminate the session, cancel inference and finalize the transcript."""
        LOGGER.info("Game shutdown requested close_resolver=%s", close_resolver)
        async with self.effects_lock:
            # If shutdown wins the lock after a delivery timeout, preserve the
            # already committed outcome before publishing the terminal event.
            await self._deliver_pending()
            async with self.lock:
                already_ended = self.state is GameState.ENDED
                self.state = GameState.ENDED
                self.generation += 1
                self.active_player_id = None
                self.round_paused = False
                self.pending_delivery.clear()
                task = self.inference_task
                if task is not None:
                    task.cancel()
            try:
                await self.transcript.finalize()
            except OSError:
                LOGGER.warning("Could not finalize game transcript")
            if not already_ended:
                await self._broadcast(
                    ServerEvent(type="game_ended", payload={"msg": "The host ended the game."})
                )
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        if close_resolver:
            await self.resolver.close()

    async def handle_disconnect(
        self,
        client_id: str,
        *,
        expected_version: int | None = None,
        still_disconnected: Callable[[], bool] = lambda: True,
    ) -> None:
        """Mark a player disconnected and advance or idle the active turn."""
        async with self.effects_lock:
            async with self.lock:
                player = self.players.get(client_id)
                if player is None or not player.is_connected:
                    return
                if not still_disconnected() or (
                    expected_version is not None and player.connection_version != expected_version
                ):
                    return
                player.is_connected = False
                player.connection_version += 1
                player.departure_pending = True
                player.return_pending = False
                directive = None
                if self.state is GameState.ACTIVE_TURN:
                    if self.active_player_id == client_id:
                        if any(p.is_connected for p in self.players.values()):
                            self.round_buffer[client_id] = IDLE_ACTION
                            self.turn_queue.rotate(-1)
                        else:
                            self.active_player_id = None
                    directive = self._next_turn_locked()
                    actions = self._take_complete_round_locked()
                    if actions is not None:
                        self._launch_round_locked(actions)
            await self._broadcast(
                ServerEvent(type="system_msg", payload={"msg": f"{player.name} disconnected."})
            )
            await self._broadcast(self._player_roster_event())
            if directive is not None:
                await self._broadcast(directive)

    def _action_allowed(self, client_id: str) -> None:
        """Raise if the client cannot submit an action in the current state."""
        player = self.players.get(client_id)
        if player is None or not player.is_connected:
            raise ValueError("Authenticate before submitting an action.")
        if self.state is not GameState.ACTIVE_TURN:
            raise ValueError("Actions are blocked while no turn is active.")
        if self.pending_delivery:
            raise ValueError("The preceding outcome is still being delivered; please wait.")
        if client_id != self.active_player_id:
            raise ValueError("It is not your turn.")

    async def _submit_action(self, client_id: str, data: dict[str, object]) -> None:
        """Validate and buffer a player action, launching a round when complete."""
        action = clean_text(data.get("action"), "action", 4_000)
        action_id = data.get("action_id")
        if action_id is not None:
            if data.get("session_id") != self.session_id:
                raise ValueError("This action belongs to another session.")
            previous = self.accepted_actions.get((client_id, action_id))
            if previous is not None:
                if previous["action"] != action or previous["round_number"] != data.get(
                    "round_number"
                ):
                    raise ValueError("An action ID cannot be reused for a different action.")
                await self.sender.send_personal(
                    client_id, ServerEvent(type="action_accepted", payload=previous)
                )
                return
            if data.get("round_number") != self.round_counter + 1:
                raise ValueError("This action belongs to another round.")
        async with self.lock:
            if not CURRENT_OWNER.get()():
                return
            self._action_allowed(client_id)
            epoch = self.generation
            candidate = dict(self.round_buffer)
            candidate[client_id] = action
            participants = self._participants_locked()
            named = self._round_actions(candidate, participants)
            current_state = self.current_scenario_state or ""
        try:
            await self.resolver.preflight_round(named, current_state)
        except LLMResolutionError as exc:
            raise ValueError(str(exc)) from exc
        async with self.effects_lock:
            async with self.lock:
                if not CURRENT_OWNER.get()() or epoch != self.generation:
                    return
                self._action_allowed(client_id)
                if participants != self._participants_locked():
                    raise ValueError("Party presence changed; please submit your action again.")
                player = self.players[client_id]
                self.round_buffer[client_id] = action
                if action_id is not None:
                    self.accepted_actions[(client_id, action_id)] = {
                        "session_id": self.session_id,
                        "action_id": action_id,
                        "round_number": self.round_counter + 1,
                        "action": action,
                    }
                action_event = ServerEvent(
                    type="action_echo",
                    payload={
                        "round_number": self.round_counter + 1,
                        "player_name": player.name,
                        "player_color_index": player.join_index,
                        "action": action,
                        "action_id": action_id,
                    },
                )
                self.turn_queue.rotate(-1)
                directive = self._next_turn_locked()
                actions = self._take_complete_round_locked()
                if actions is not None:
                    self._launch_round_locked(actions)
            LOGGER.info(
                "Player action accepted round=%d player=%r",
                action_event.payload["round_number"],
                action_event.payload["player_name"],
            )
            await self._broadcast(action_event)
            if action_id is not None:
                await self.sender.send_personal(
                    client_id,
                    ServerEvent(
                        type="action_accepted",
                        payload=self.accepted_actions[(client_id, action_id)],
                    ),
                )
            if directive is not None:
                await self._broadcast(directive)

    def _next_turn_locked(self) -> ServerEvent | None:
        """Advance the turn queue and return a directive for the next active player."""
        if self.state is not GameState.ACTIVE_TURN:
            return None
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
        """Return non-idle actions keyed by player name."""
        return {
            self.players[client_id].name: action
            for client_id, action in self.round_buffer.items()
            if action != IDLE_ACTION
        }

    def _take_complete_round_locked(self) -> dict[str, str] | None:
        """Return the round's actions when every player has submitted."""
        if not self.players or len(self.round_buffer) != len(self.players):
            return None
        self.state = GameState.AWAITING_LLM
        return dict(self.round_buffer)

    def _participants_locked(self) -> dict[str, Participant]:
        return {
            item: Participant(p.name, p.departure_pending, p.return_pending, p.connection_version)
            for item in self.join_order
            if (p := self.players[item]).is_connected or p.departure_pending or p.return_pending
        }

    @staticmethod
    def _round_actions(
        actions: dict[str, str], participants: dict[str, Participant]
    ) -> RoundActions:
        return RoundActions(
            {p.name: actions.get(item, IDLE_ACTION) for item, p in participants.items()},
            {p.name: p for p in participants.values()},
        )

    def _launch_round_locked(self, actions: dict[str, str], *, retry: bool = False) -> None:
        """Prepare and launch a round resolution job from buffered actions."""
        if not retry:
            self.pending_resolution = PendingRound(
                dict(actions), self._participants_locked(), self.current_scenario_state or ""
            )
        self._launch_job_locked(self._resolve_round, GameState.AWAITING_LLM)

    async def _retry_round(self, client_id: str, data: dict[str, object]) -> None:
        """Re-run a paused round with its retained actions and dice."""
        del data
        async with self.lock:
            if not CURRENT_OWNER.get()():
                return
            player = self.players.get(client_id)
            if player is None or not player.is_host:
                raise ValueError("Only the host can retry a paused round.")
            if not self.round_paused or self.pending_resolution is None:
                raise ValueError("No paused round to retry.")
            pending = self.pending_resolution
            finishing_task = self.inference_task

        # The pause error is sent before _run_job finishes publishing usage and
        # clearing its task pointer. Wait outside the state lock, then recheck:
        # the host may have ended the game while cleanup was in progress.
        if finishing_task is not None and not finishing_task.done():
            await asyncio.gather(finishing_task, return_exceptions=True)

        async with self.lock:
            if not CURRENT_OWNER.get()():
                return
            player = self.players.get(client_id)
            if player is None or not player.is_host:
                raise ValueError("Only the host can retry a paused round.")
            if (
                not self.round_paused
                or self.pending_resolution is None
                or self.pending_resolution is not pending
            ):
                raise ValueError("No paused round to retry.")
            if self.inference_task is not None and not self.inference_task.done():
                raise ValueError("The previous request is still finishing.")
            self._launch_round_locked(self.round_buffer.copy(), retry=True)
        LOGGER.info("Paused round retry accepted round=%d", self.round_counter + 1)

    async def _resolve_round(self, epoch: int) -> None:
        """Measure all round work, including failed host attempts."""
        self.resolver.begin_round_usage(self.round_counter + 1)
        error = None
        try:
            await self._resolve_round_work(epoch)
        except BaseException as exc:
            error = type(exc).__name__
            raise
        finally:
            self.resolver.finish_round_usage(error)

    async def _resolve_round_work(self, epoch: int) -> None:
        """Plan authoritative checks, resolve all actions, and commit one round.

        Planning and generation happen outside the state lock.  The final state
        mutation is guarded by both the generation check and the effects lock;
        public output is assembled only after private checks have been separated.
        """
        pending = self.pending_resolution
        assert pending is not None
        actions = pending.actions
        participants = pending.participants
        llm_actions = self._round_actions(actions, participants)
        reused_dice = pending.dice is not None
        if pending.dice is None:
            plan = await self.resolver.plan_dice(llm_actions, pending.previous_state)
            if set(plan.rolls) != set(llm_actions) or not set(plan.hidden_rolls) <= {
                name for name, required in plan.rolls.items() if required
            }:
                raise LLMResolutionError("Invalid dice plan participants.")
            try:
                chance_events = validate_chance_events(
                    plan.chance_events,
                    self.private_guidance,
                    preserve_occurrences=self.chance_rule is not None,
                )
            except ValueError as exc:
                raise LLMResolutionError(str(exc)) from exc
            pending.hidden = set(plan.hidden_rolls)
            pending.chance_events = [roll_chance(event) for event in chance_events]
            pending.dice = {name: roll_d100() for name, required in plan.rolls.items() if required}
        if self.private_guidance:
            LOGGER.info(
                "Private guidance checks round=%d generation=%d reused=%s rolls=%s",
                self.round_counter + 1,
                epoch,
                reused_dice,
                json.dumps(
                    {name: pending.dice[name] for name in sorted(pending.hidden)},
                    ensure_ascii=True,
                ),
            )
        if pending.chance_events:
            LOGGER.info(
                "Private chance events round=%d generation=%d reused=%s results=%s",
                self.round_counter + 1,
                epoch,
                reused_dice,
                json.dumps([result.model_dump() for result in pending.chance_events]),
            )
        prepared = await self.resolver.stage_resolution(
            llm_actions,
            pending.dice,
            pending.hidden,
            **({"chance_events": pending.chance_events} if pending.chance_events else {}),
        )
        resolution = prepared.result
        if (
            set(resolution.player_resolutions) != set(llm_actions)
            or not resolution.global_narrative.strip()
            or any(not text.strip() for text in resolution.player_resolutions.values())
        ):
            raise LLMResolutionError("Invalid resolution participants or empty narrative.")
        public_dice = {
            name: value for name, value in pending.dice.items() if name not in pending.hidden
        }
        display_actions = {p.name: actions[item] for item, p in participants.items()}
        display = resolution
        async with self.effects_lock:
            async with self.lock:
                if not self._job_current(epoch):
                    return
                self.resolver.commit_resolution(prepared)
                self.round_counter += 1
                number = self.round_counter
                self.accepted_actions = {
                    key: value
                    for key, value in self.accepted_actions.items()
                    if value["round_number"] >= number - 1
                }
                for item, participant in participants.items():
                    player = self.players[item]
                    if player.connection_version == participant.connection_version:
                        if participant.departed:
                            player.departure_pending = False
                        if participant.returned:
                            player.return_pending = False
                self.current_scenario_state = resolution.global_narrative
                self.round_buffer.clear()
                self.turn_queue = deque(self.join_order)
                self.pending_resolution = None
                self.state = GameState.ACTIVE_TURN
                directive = self._next_turn_locked()
                self.latest_round = {
                    **display.model_dump(),
                    "round_number": number,
                    "submitted_actions": {
                        name: action
                        for name, action in display_actions.items()
                        if action != IDLE_ACTION
                    },
                    "player_order": [self.players[item].name for item in self.join_order],
                    "dice_results": public_dice,
                }
                self.pending_delivery.append(
                    ServerEvent(type="state_update", payload=dict(self.latest_round))
                )
                if directive is not None:
                    self.pending_delivery.extend(
                        [
                            ServerEvent(type="round_start", payload={"round_number": number + 1}),
                            directive,
                        ]
                    )
            try:
                await self.transcript.append_round(
                    number,
                    display_actions,
                    display,
                    public_dice,
                    player_colors={
                        self.players[item].name: self.players[item].join_index
                        for item in participants
                    },
                    hidden_dice_results={
                        name: value
                        for name, value in pending.dice.items()
                        if name in pending.hidden
                    },
                    chance_events=pending.chance_events,
                )
            except (OSError, RuntimeError):
                LOGGER.warning("Could not append round %s to transcript", number)
            try:
                await self.resolver.complete_round_debug(
                    number,
                    self._round_debug_summary(
                        number,
                        actions,
                        participants,
                        llm_actions,
                        pending,
                        resolution,
                        display,
                        display_actions,
                    ),
                )
            except Exception:
                LOGGER.exception("Could not finalize round diagnostics round=%d", number)
            await self._deliver_pending()

    def _round_debug_summary(
        self,
        number: int,
        actions: dict[str, str],
        participants: dict[str, Participant],
        llm_actions: dict[str, str],
        pending: PendingRound,
        resolution: RoundResolution,
        display: RoundResolution,
        display_actions: dict[str, str],
    ) -> dict[str, object]:
        """Capture engine-owned private state for the local full-round archive."""
        participant_summary = {}
        for participant in participants.values():
            participant_summary[participant.name] = {
                "departed": participant.departed,
                "returned": participant.returned,
                "connection_version": participant.connection_version,
            }
        dice = pending.dice or {}
        hidden = pending.hidden
        return {
            "round_number": number,
            "private_guidance": self.private_guidance,
            "actions_by_client": actions,
            "actions_sent_to_resolver": llm_actions,
            "actions_displayed_to_players": display_actions,
            "participants": participant_summary,
            "previous_scenario_state": pending.previous_state,
            "authoritative_dice": dice,
            "hidden_dice": {name: value for name, value in dice.items() if name in hidden},
            "chance_events": [event.model_dump() for event in pending.chance_events],
            "raw_resolution": resolution.model_dump(),
            "published_resolution": display.model_dump(),
            "scenario_state_after_round": self.current_scenario_state,
        }

    async def _refresh_usage(self) -> None:
        """Bound optional measurement outside state/effects locks and job deadlines."""
        try:
            async with asyncio.timeout(USAGE_REFRESH_TIMEOUT_SECONDS):
                await self.resolver.refresh_usage()
        except Exception as exc:
            LOGGER.warning("Usage refresh unavailable error=%s", type(exc).__name__)

    async def _publish_usage(self, client_id: str | None = None) -> None:
        """Project private diagnostics separately from public aggregate usage."""
        snapshot = self.resolver.usage_snapshot()
        private_keys = {
            "round_by_kind",
            "last_request",
            "round_failures",
            "last_round_error",
            "round_work_seconds",
        }
        public = {key: value for key, value in snapshot.items() if key not in private_keys}
        for key in ("round", "game"):
            if isinstance(public.get(key), dict):
                public[key] = {
                    name: value for name, value in public[key].items() if name != "latency_seconds"
                }
        event = ServerEvent(type="token_usage", payload=public)
        if client_id is not None:
            player = self.players.get(client_id)
            await self.sender.send_personal(
                client_id,
                ServerEvent(
                    type="token_usage", payload=snapshot if player and player.is_host else public
                ),
            )
        else:
            await self._broadcast(event)
            for player in self.players.values():
                if player.is_host and player.is_connected:
                    await self.sender.send_personal(
                        player.client_id, ServerEvent(type="token_usage", payload=snapshot)
                    )

    async def _send_error(self, client_id: str, message: str) -> None:
        """Send a personal error event to a client."""
        await self.sender.send_personal(
            client_id, ServerEvent(type="error", payload={"msg": message})
        )
