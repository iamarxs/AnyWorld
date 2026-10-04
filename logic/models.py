"""Internal game domain types and dependency protocols."""

from dataclasses import dataclass, field
import secrets
from enum import Enum, auto
from typing import Any, Protocol

from core.schemas import (
    ChanceEventResult,
    DicePlan,
    RoundResolution,
    ServerEvent,
    StructuredChanceRule,
)


@dataclass(frozen=True, slots=True)
class Participant:
    name: str
    departed: bool
    returned: bool
    connection_version: int


class RoundActions(dict[str, str]):
    """Untrusted attempts with separately owned server presence metadata."""

    def __init__(self, actions: dict[str, str], presence: dict[str, Participant]):
        super().__init__(actions)
        self.presence = dict(presence)


@dataclass(slots=True)
class PendingRound:
    actions: dict[str, str]
    participants: dict[str, Participant]
    previous_state: str
    dice: dict[str, int] | None = None
    hidden: set[str] = field(default_factory=set)
    chance_events: list[ChanceEventResult] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class PreparedResolution:
    result: RoundResolution
    memory: dict[str, str] | None = None
    history: tuple[dict[str, str], ...] = ()
    names: tuple[str, ...] = ()
    revision: int = 0


class GameState(Enum):
    """States of the game session lifecycle."""

    AWAITING_HOST = auto()
    AWAITING_PLAYERS = auto()
    SCENARIO_INJECTION = auto()
    ACTIVE_TURN = auto()
    AWAITING_LLM = auto()
    ENDED = auto()


@dataclass(slots=True)
class Player:
    """A participant in the session, tracked across disconnects and returns."""

    client_id: str
    name: str
    is_host: bool
    join_index: int = 0
    is_connected: bool = True
    departure_pending: bool = False
    return_pending: bool = False
    connection_version: int = 0
    reconnect_token: str = field(default_factory=lambda: secrets.token_urlsafe(32))


class EventSender(Protocol):
    """Protocol for broadcasting and personal server events."""

    async def broadcast_global(self, event: ServerEvent) -> None:
        """Broadcast an event to all active connections."""
        ...

    async def send_personal(self, client_id: str, event: ServerEvent) -> None:
        """Send an event to a single client."""
        ...

    async def broadcast_except(self, client_id: str, event: ServerEvent) -> None:
        """Broadcast an event to all connections except one client."""
        ...


class ResolutionManager(Protocol):
    """Protocol for the LLM resolution backend."""

    def begin_round_usage(self, number: int) -> None:
        """Group inference consumption, retaining totals across host retries."""
        ...

    def usage_snapshot(self) -> dict[str, Any]:
        """Return prompt-free round/game totals and estimated context occupancy."""
        ...

    def finish_round_usage(self, error: str | None = None) -> None:
        """Finish timing round work, excluding the human wait before a retry."""
        ...

    async def complete_round_debug(self, number: int, engine_summary: dict[str, Any]) -> None:
        """Persist the private full-round diagnostic after a committed round."""
        ...

    def set_genesis(self, scenario: str, guidance: str = "") -> None:
        """Set the initial scenario and optional guidance."""
        ...

    async def prepare_chance_rule(self) -> None:
        """Normalize the private conditional percentage rule before the first action."""
        ...

    async def generate_scenario_title(self) -> str:
        """Generate only the scenario title before players join."""
        ...

    async def plan_dice(self, round_buffer: dict[str, str], current_state: str = "") -> DicePlan:
        """Plan which actions need a d100 check."""
        ...

    async def preflight_round(self, actions: dict[str, str], current_state: str = "") -> None:
        """Reject actions that exceed the context budget."""
        ...

    async def refresh_usage(self) -> None: ...

    async def stage_start_state(self, player_names: list[str]) -> PreparedResolution: ...

    async def stage_resolution(
        self,
        actions: dict[str, str],
        dice_results: dict[str, int],
        hidden_rolls: set[str],
        chance_events: list[ChanceEventResult] | None = None,
    ) -> PreparedResolution: ...

    def commit_resolution(self, prepared: PreparedResolution) -> None: ...

    def configure_chance_rule(self, rule: StructuredChanceRule | None) -> None: ...

    async def close(self) -> None:
        """Release backend resources."""
        ...
