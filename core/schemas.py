"""Strict WebSocket and LLM data contracts."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict


class StrictModel(BaseModel):
    """Base model that forbids coercion and unknown fields."""

    model_config = ConfigDict(strict=True, extra="forbid")


class ClientPayload(StrictModel):
    event_type: Literal["auth", "chat", "action", "scenario_init", "start_game"]
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
    ]
    payload: dict[str, Any]


class RoundResolution(StrictModel):
    round_title: str | None = None
    global_narrative: str
    player_resolutions: dict[str, str]
