"""Strict WebSocket and LLM data contracts."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Base model that forbids coercion and unknown fields."""

    model_config = ConfigDict(strict=True, extra="forbid")


class ClientPayload(StrictModel):
    """Envelope for a client-to-server WebSocket message."""

    event_type: Literal[
        "auth", "chat", "action", "scenario_init", "start_game", "end_game", "retry_round"
    ]
    data: dict[str, Any]


class ServerEvent(StrictModel):
    """Envelope for a server-to-client WebSocket message."""

    type: Literal[
        "state_update",
        "chat_echo",
        "turn_directive",
        "error",
        "system_msg",
        "auth_ok",
        "scenario_ready",
        "round_start",
        "action_echo",
        "player_roster",
        "dm_thinking",
        "game_ended",
        "token_usage",
    ]
    payload: dict[str, Any]


class ChanceEvent(StrictModel):
    """One applicable occurrence of a percentage rule from private guidance."""

    source_rule: str = Field(min_length=1, max_length=1_000)
    trigger: Literal["per_round", "condition"]
    occurrence: str = Field(min_length=1, max_length=240)
    chance_percent: int = Field(ge=0, le=100)


class ChanceEventResult(StrictModel):
    """Private authoritative event outcome, separate from action-quality dice."""

    event: ChanceEvent
    roll: int = Field(ge=1, le=100)
    occurred: bool


class ChanceRuleDecision(StrictModel):
    """One authoritative list of occurrences for a private rule this round."""

    trigger: Literal["per_round", "condition"]
    occurrences: list[str] = Field(max_length=16)
    reason: str = Field(min_length=1, max_length=240)


class DicePlan(StrictModel):
    """LLM-selected checks; hidden names are never disclosed to clients."""

    rolls: dict[str, bool]
    hidden_rolls: list[str]
    hidden_roll_sources: dict[str, str] = Field(default_factory=dict)
    chance_events: list[ChanceEvent] = Field(default_factory=list, max_length=16)
    chance_rule_decisions: dict[str, ChanceRuleDecision] = Field(default_factory=dict)


class ContextSummary(StrictModel):
    """Structured memory retained after older round history is compacted."""

    world_state: str
    player_states: dict[str, str]
    important_npcs: str
    unresolved_threads: list[str]


class RoundResolution(StrictModel):
    """Structured outcome of a resolved round."""

    round_title: str | None = None
    global_narrative: str
    player_resolutions: dict[str, str]


class ScenarioTitle(StrictModel):
    """Title-only preparation before the party has joined."""

    title: str


class SummaryAudit(StrictModel):
    """Private check of a proposed memory checkpoint against its source context."""

    preserved: bool
    corrections: list[str]
