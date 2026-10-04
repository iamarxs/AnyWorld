"""Shared deterministic clients and resolver contracts for offline tests."""

from types import SimpleNamespace

from core.schemas import (
    AuditVerdict,
    ContextSummary,
    DicePlan,
    RoundResolution,
    ScenarioTitle,
    ServerEvent,
    SummaryAudit,
)
from logic.models import PreparedResolution
from logic.presentation import name_resolution


class FakeSender:
    """In-memory event sender that records sent events."""

    def __init__(self) -> None:
        """Initialize the event recorder."""
        self.events: list[tuple[str | None, ServerEvent]] = []

    async def broadcast_global(self, event: ServerEvent) -> None:
        """Record a global event."""
        self.events.append((None, event))

    async def send_personal(self, client_id: str, event: ServerEvent) -> None:
        """Record a personal event."""
        self.events.append((client_id, event))

    async def broadcast_except(self, client_id: str, event: ServerEvent) -> None:
        """Record a broadcast-except event."""
        self.events.append((f"except:{client_id}", event))

    def events_of_type(self, event_type: str) -> list[ServerEvent]:
        """Return recorded events of a given type."""
        return [event for _, event in self.events if event.type == event_type]


class FakeResolver:
    """Deterministic resolution backend for tests."""

    def configure_chance_rule(self, rule) -> None:
        self.chance_rule = rule

    def __init__(self) -> None:
        """Initialize the fake resolver state."""
        self.scenario = ""
        self.rounds = 0
        self.start_names = []

    def set_genesis(self, scenario: str, guidance: str = "") -> None:
        """Record the scenario."""
        self.scenario = scenario

    async def generate_scenario_title(self) -> str:
        """Return only a fixed title."""
        return "The Test Quest"

    async def generate_start_state(self, player_names: list[str]) -> RoundResolution:
        """Return a fixed start state."""
        self.start_names.append(list(player_names))
        return RoundResolution(
            global_narrative=f"{', '.join(player_names)} stand at a gate.",
            player_resolutions={},
        )

    async def generate_resolution(
        self, round_buffer: dict[str, str], dice_results=None, hidden_rolls=None, chance_events=None
    ) -> RoundResolution:
        """Return a fixed resolution for the given actions."""
        self.rounds += 1
        return RoundResolution(
            global_narrative=f"State after round {self.rounds}.",
            player_resolutions={
                name: f"Resolved: {action}" for name, action in round_buffer.items()
            },
        )

    async def prepare_chance_rule(self):
        pass

    async def preflight_round(self, actions, current_state=""):
        pass

    async def plan_dice(self, actions, current_state=""):
        return DicePlan(rolls={name: False for name in actions}, hidden_rolls=[])

    async def stage_start_state(self, names):
        return PreparedResolution(await self.generate_start_state(names))

    async def stage_resolution(self, actions, dice_results, hidden_rolls, chance_events=None):
        result = await self.generate_resolution(
            actions,
            dice_results,
            hidden_rolls,
            **({"chance_events": chance_events} if chance_events else {}),
        )
        result = result.model_copy(
            update={
                "player_resolutions": {
                    name: name_resolution(name, text)
                    for name, text in result.player_resolutions.items()
                }
            }
        )
        return PreparedResolution(result)

    def commit_resolution(self, prepared):
        pass

    def begin_round_usage(self, number):
        pass

    def finish_round_usage(self, error=None):
        pass

    async def complete_round_debug(self, number, summary):
        pass

    async def refresh_usage(self):
        pass

    def usage_snapshot(self):
        return {
            "retained_context_tokens": getattr(self, "last_token_usage", 0),
            "context_window_size": 8192,
            "counting_method": "estimate",
        }

    async def close(self):
        pass


class FakeClient:
    """Fake OpenAI client that returns a configured result."""

    def __init__(self, result=None, finish_reason="stop"):
        """Initialize the fake client."""
        self.calls = []
        self.result = result
        self.finish_reason = finish_reason
        self.closed = False
        self.beta = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(parse=self.parse))
        )

    async def parse(self, **kwargs):
        """Record the call and return a configured result."""
        self.calls.append(kwargs)
        result = self.result
        if callable(result):
            result = result(kwargs)
        elif isinstance(result, DicePlan) and kwargs["response_format"] in (
            AuditVerdict,
            SummaryAudit,
        ):
            result = kwargs["response_format"](preserved=True, corrections=[])
        if isinstance(result, Exception):
            raise result
        if result is None:
            schema = kwargs["response_format"]
            if issubclass(schema, DicePlan):
                names = schema.model_json_schema()["properties"]["rolls"].get("required", ["Alice"])
                result = DicePlan(
                    rolls={name: True for name in names},
                    hidden_rolls=[],
                )
            elif schema is ScenarioTitle:
                result = ScenarioTitle(title="The gate")
            elif schema in (AuditVerdict, SummaryAudit):
                result = schema(preserved=True, corrections=[])
            elif issubclass(schema, ContextSummary):
                result = memory()
            else:
                result = RoundResolution(
                    global_narrative="A breeze rises.",
                    player_resolutions={
                        name: f"{name} waits."
                        for name in schema.model_json_schema()["properties"][
                            "player_resolutions"
                        ].get("required", ["Alice"])
                    },
                )
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(parsed=result), finish_reason=self.finish_reason
                )
            ],
            usage=SimpleNamespace(total_tokens=100),
        )

    async def close(self):
        """Record the close."""
        self.closed = True


def memory():
    """Return a sample durable memory summary."""
    return ContextSummary(
        world_state="The north gate remains locked. The vial was consumed.",
        player_states={"Alice": "Broken wrist; carries the brass key; at the north gate."},
        important_npcs="Guard Mira trusts Alice.",
        unresolved_threads=["Return Mira's key before dusk."],
    )
