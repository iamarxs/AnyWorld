"""Strict WebSocket and LLM data contracts."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Base model that forbids coercion and unknown fields."""

    model_config = ConfigDict(strict=True, extra="forbid")


class ClientPayload(StrictModel):
    event_type: Literal[
        "auth", "chat", "action", "scenario_init", "start_game", "end_game", "retry_round"
    ]
    data: dict[str, Any]


class ServerEvent(StrictModel):
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


class DicePlan(StrictModel):
    """LLM-selected checks; hidden names are never disclosed to clients."""

    rolls: dict[str, bool]
    hidden_rolls: list[str]


class ContextSummary(StrictModel):
    """Structured memory retained after older round history is compacted."""

    world_state: str
    player_states: dict[str, str]
    important_npcs: str
    unresolved_threads: list[str]


class RoundResolution(StrictModel):
    round_title: str | None = None
    global_narrative: str
    player_resolutions: dict[str, str]
