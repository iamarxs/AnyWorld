"""Internal game domain types and dependency protocols."""

from dataclasses import dataclass, field
import secrets
from enum import Enum, auto
from typing import Any, Protocol

from core.schemas import DicePlan, RoundResolution, ServerEvent


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

    def set_genesis(self, scenario: str, guidance: str = "") -> None:
        """Set the initial scenario and optional guidance."""
        ...

    async def discover_context_window(self) -> None:
        """Discover the backend context window size."""
        ...

    async def generate_initial_state(self) -> RoundResolution:
        """Generate the initial scenario state."""
        ...

    async def generate_start_state(self, player_names: list[str]) -> RoundResolution:
        """Introduce the joined players at game start."""
        ...

    async def plan_dice(self, round_buffer: dict[str, str], current_state: str = "") -> DicePlan:
        """Plan which actions need a d100 check."""
        ...

    async def generate_resolution(
        self,
        round_buffer: dict[str, str],
        dice_results: dict[str, int] | None = None,
        hidden_rolls: set[str] | None = None,
    ) -> RoundResolution:
        """Resolve a round of actions."""
        ...

    async def preflight_round(self, actions: dict[str, str], current_state: str = "") -> None:
        """Reject actions that exceed the context budget."""
        ...

    async def close(self) -> None:
        """Release backend resources."""
        ...
