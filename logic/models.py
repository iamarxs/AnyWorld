"""Internal game domain types and dependency protocols."""

from dataclasses import dataclass
from enum import Enum, auto
from typing import Protocol

from core.schemas import RoundResolution, ServerEvent


class GameState(Enum):
    AWAITING_HOST = auto()
    AWAITING_PLAYERS = auto()
    SCENARIO_INJECTION = auto()
    ACTIVE_TURN = auto()
    AWAITING_LLM = auto()


@dataclass(slots=True)
class Player:
    client_id: str
    name: str
    is_host: bool
    is_connected: bool = True
    departure_pending: bool = False
    return_pending: bool = False


class EventSender(Protocol):
    async def broadcast_global(self, event: ServerEvent) -> None: ...

    async def send_personal(self, client_id: str, event: ServerEvent) -> None: ...


class ResolutionManager(Protocol):
    def set_genesis(self, scenario: str, guidance: str = "") -> None: ...

    async def generate_initial_state(self) -> RoundResolution: ...

    async def generate_resolution(self, round_buffer: dict[str, str]) -> RoundResolution: ...
