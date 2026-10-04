"""Strict WebSocket and LLM data contracts."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator


class StrictModel(BaseModel):
    """Base model that forbids coercion and unknown fields."""

    model_config = ConfigDict(strict=True, extra="forbid")
    _provider_response_text: str | None = PrivateAttr(default=None)


class ClientPayload(StrictModel):
    """Envelope for a client-to-server WebSocket message."""

    event_type: Literal[
        "auth",
        "chat",
        "action",
        "scenario_init",
        "start_game",
        "end_game",
        "new_game",
        "retry_round",
        "journal_request",
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
        "journal_page",
        "action_accepted",
    ]
    payload: dict[str, Any]


class ChanceEvent(StrictModel):
    """One applicable occurrence of a percentage rule from private guidance."""

    source_rule: str = Field(min_length=1, max_length=1_400)
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
    occurrences: list[str] = Field(max_length=100)


class ChanceRuleInterpretation(StrictModel):
    """Private normalized meaning of a host-authored chance rule."""

    trigger_type: Literal["action", "world_transition"]
    trigger_description: str = Field(min_length=1, max_length=240)
    occurrence_scope: Literal["per_player", "shared"]
    effect: str = Field(min_length=1, max_length=500)
    eligibility: str = Field(default="", max_length=500)
    cadence: Literal["per_round", "condition"] = "condition"
    structured: bool = False


class StructuredChanceRule(StrictModel):
    chance_percent: int = Field(ge=0, le=100)
    cadence: Literal["per_round", "condition"]
    trigger: str = Field(default="", max_length=240)
    eligibility: str = Field(default="", max_length=500)
    effect: str = Field(min_length=1, max_length=500)
    scope: Literal["shared", "per_player"]

    @model_validator(mode="after")
    def explicit_semantics(self):
        import re

        if self.cadence == "condition" and not self.trigger.strip():
            raise ValueError("Conditional cadence requires an explicit occurrence trigger.")
        if self.cadence == "per_round" and self.trigger.strip():
            raise ValueError("A per-round rule cannot also specify an occurrence trigger.")
        for value in (self.trigger, self.eligibility, self.effect):
            if re.search(r"%|\bpercent\b|[\x00-\x1f\x7f-\x9f]", value, re.IGNORECASE):
                raise ValueError("Rule text must be a single line without extra percentages.")
        if not self.effect.strip():
            raise ValueError("A chance rule requires an effect.")
        return self


class ChanceTriggerPlan(StrictModel):
    """Exact participant matches for the normalized private chance rule this round."""

    occurrences: list[str] = Field(max_length=100)


class DicePlan(StrictModel):
    """LLM-selected checks; hidden names are never disclosed to clients."""

    rolls: dict[str, bool]
    hidden_rolls: list[str]
    hidden_roll_sources: dict[str, str] = Field(default_factory=dict)
    chance_events: list[ChanceEvent] = Field(default_factory=list, max_length=100)


class ContextSummary(StrictModel):
    """Structured memory retained after older round history is compacted."""

    world_state: str
    player_states: dict[str, str]
    important_npcs: str
    unresolved_threads: list[str]


class RoundResolution(StrictModel):
    """Structured outcome of a resolved round."""

    player_resolutions: dict[str, str]
    global_narrative: str


class ScenarioTitle(StrictModel):
    """Title-only preparation before the party has joined."""

    title: str = Field(min_length=1, max_length=80)


class SummaryAudit(StrictModel):
    """Private check of a proposed memory checkpoint against its source context."""

    preserved: bool
    corrections: list[str]


class AuditVerdict(StrictModel):
    """Private yes/no verdict for hidden-check and chance-outcome validation."""

    preserved: bool
    corrections: list[str] = Field(default_factory=list)


class AuthInput(StrictModel):
    name: str = Field(min_length=1, max_length=40)
    password_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    reconnect_token: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class ChatInput(StrictModel):
    message: str = Field(min_length=1, max_length=1000)


class ActionInput(StrictModel):
    action: str = Field(min_length=1, max_length=4000)
    action_id: str | None = Field(default=None, min_length=1, max_length=64)
    session_id: str | None = Field(default=None, max_length=36)
    round_number: int | None = Field(default=None, ge=1)


class ScenarioInput(StrictModel):
    scenario: str = Field(min_length=1, max_length=20000)
    guidance: str = Field(default="", max_length=5000)
    chance_event: str = Field(default="", max_length=1000)
    chance_rule: StructuredChanceRule | None = None


class JournalInput(StrictModel):
    after: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=100)
    search: str = Field(default="", max_length=200)
    mode: Literal["replay", "history", "export"] = "history"


def validate_client_data(payload: ClientPayload) -> None:
    schema = {
        "auth": AuthInput,
        "chat": ChatInput,
        "action": ActionInput,
        "scenario_init": ScenarioInput,
        "journal_request": JournalInput,
    }.get(payload.event_type, StrictModel)
    schema.model_validate(payload.data)
