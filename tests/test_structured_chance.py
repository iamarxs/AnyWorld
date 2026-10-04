"""Host-selected cadence, eligibility and scope are independent contracts."""

import asyncio

import pytest
from pydantic import ValidationError

from core.config import settings
from core.schemas import StructuredChanceRule, ChanceTriggerPlan, DicePlan, RoundResolution
from logic.dice import structured_rule_text, validate_legacy_rule, roll_chance
from logic.llm_manager import LLMContextManager
from support import FakeClient, memory


def rule(**changes):
    return StructuredChanceRule(
        chance_percent=40, cadence="per_round", effect="A bell rings", scope="per_player", **changes
    )


@pytest.mark.parametrize(
    "eligibility,eligible",
    [("", ["Alice", "Bob"]), ("Inside the tower", ["Alice"]), ("Inside the tower", [])],
)
def test_per_round_scope_respects_optional_eligibility(eligibility, eligible):
    async def run():
        spec = rule(eligibility=eligibility)
        calls = []

        def response(kwargs):
            calls.append(kwargs["response_format"])
            if kwargs["response_format"] is ChanceTriggerPlan:
                return ChanceTriggerPlan(occurrences=eligible)
            return DicePlan(rolls={"Alice": False, "Bob": False}, hidden_rolls=[])

        manager = LLMContextManager(FakeClient(response))
        manager.set_genesis("A tower", structured_rule_text(spec))
        manager.configure_chance_rule(spec)
        plan = await manager.plan_dice(
            {"Alice": "Wait", "Bob": "Wait"}, "Alice is inside the tower."
        )
        assert len(plan.chance_events) == len(eligible)
        assert all(event.trigger == "per_round" for event in plan.chance_events)
        assert (ChanceTriggerPlan in calls) == bool(eligibility)
        await manager.close()

    asyncio.run(run())


def test_explicit_trigger_and_percent_boundaries():
    with pytest.raises(ValidationError, match="explicit occurrence"):
        StructuredChanceRule(
            chance_percent=0, cadence="condition", effect="A bell rings", scope="shared"
        )
    with pytest.raises(ValidationError, match="extra percentages"):
        rule(eligibility="Another 20% rule")
    for text in ("A 40% chance a bell rings", "40% per round when someone enters"):
        with pytest.raises(ValueError, match="ambiguous"):
            validate_legacy_rule(text)
    validate_legacy_rule("40% per round a bell rings")


@pytest.mark.parametrize("stale_memory", [False, True])
@pytest.mark.parametrize(
    "fact",
    [
        "Alice acquired the brass key.",
        "Alice consumed the brass key.",
        "Alice broke her leg.",
        "Alice moved into the tower.",
    ],
)
def test_chance_pass_receives_recent_player_outcomes(stale_memory, fact):
    async def run():
        spec = rule(eligibility="Carrying the brass key, uninjured, inside the tower")
        client = FakeClient(ChanceTriggerPlan(occurrences=["Alice"]))
        manager = LLMContextManager(client)
        manager.set_genesis("A tower", structured_rule_text(spec))
        manager.configure_chance_rule(spec)
        if stale_memory:
            manager.memory = {
                "role": "user",
                "content": memory()
                .model_copy(
                    update={"player_states": {"Alice": "Uninjured; carrying a key outside."}}
                )
                .model_dump_json(),
            }
        manager.history = [
            {"role": "user", "content": "Alice: Inspect the tower"},
            {
                "role": "assistant",
                "content": RoundResolution(
                    global_narrative="The village bell rings.",
                    player_resolutions={"Alice": fact},
                ).model_dump_json(),
            },
        ]
        await manager._plan_chance_triggers({"Alice": "Wait"}, "The village bell rings.")
        assert fact in "\n".join(message["content"] for message in client.calls[0]["messages"])
        await manager.close()

    asyncio.run(run())


@pytest.mark.parametrize("count", [16, 17, 100])
@pytest.mark.parametrize(
    "cadence,eligibility",
    [("per_round", ""), ("per_round", "In the tower"), ("condition", "In the tower")],
)
def test_per_player_chance_capacity_through_resolution(count, cadence, eligibility):
    async def run():
        settings.server.max_players = count
        settings.llm.context_window_size = 128000
        actions = {f"Player {number}": "Wait" for number in range(count)}
        spec = StructuredChanceRule(
            chance_percent=0,
            cadence=cadence,
            trigger="The bell rings" if cadence == "condition" else "",
            effect="A raven appears",
            eligibility=eligibility,
            scope="per_player",
        )

        class Client(FakeClient):
            async def parse(self, **kwargs):
                self.result = (
                    ChanceTriggerPlan(occurrences=list(actions))
                    if kwargs["response_format"] is ChanceTriggerPlan
                    else None
                )
                return await super().parse(**kwargs)

        manager = LLMContextManager(Client())
        manager.set_genesis("A tower", structured_rule_text(spec))
        manager.configure_chance_rule(spec)
        await manager.preflight_round(actions, "The bell rings")
        plan = await manager.plan_dice(actions, "The bell rings")
        assert len(plan.chance_events) == count
        assert len({event.occurrence for event in plan.chance_events}) == count
        if eligibility:
            call = manager.client.calls[0]
            with manager.budget.chance_participants(actions):
                assert call["max_completion_tokens"] == manager.budget.request_output_limit(
                    "chance_trigger"
                )
            assert call["max_completion_tokens"] >= settings.llm.dice_output_tokens
        results = [roll_chance(event) for event in plan.chance_events]
        resolution = await manager.generate_resolution(actions, chance_events=results)
        assert set(resolution.player_resolutions) == set(actions)
        await manager.close()

    asyncio.run(run())


@pytest.mark.parametrize("cadence", ["per_round", "condition"])
def test_maximum_rule_text_survives_scenario_setup_and_round(tmp_path, cadence):
    from logic.engine import GameEngine, GameState
    from test_engine import authenticate, payload
    from support import FakeSender

    async def run():
        settings.llm.context_window_size = 128000
        spec = StructuredChanceRule(
            chance_percent=0,
            cadence=cadence,
            trigger="T" * 240 if cadence == "condition" else "",
            eligibility="E" * 500,
            effect="F" * 500,
            scope="per_player",
        )
        assert len(structured_rule_text(spec)) > 1000

        class Client(FakeClient):
            async def parse(self, **kwargs):
                schema = kwargs["response_format"]
                self.result = (
                    ChanceTriggerPlan(occurrences=["Alice"])
                    if schema is ChanceTriggerPlan
                    else (
                        RoundResolution(
                            global_narrative="Alice, a scout, explores the tower.",
                            player_resolutions={
                                name: f"{name} explores the tower."
                                for name in schema.model_json_schema()["properties"][
                                    "player_resolutions"
                                ].get("required", [])
                            },
                        )
                        if issubclass(schema, RoundResolution)
                        else None
                    )
                )
                return await super().parse(**kwargs)

        resolver = LLMContextManager(Client())
        sender = FakeSender()
        engine = GameEngine(sender, resolver)
        await authenticate(engine, "host", name="Alice", password=settings.server.host_password)
        await engine.process_payload(
            "host", payload("scenario_init", scenario="A tower", chance_rule=spec.model_dump())
        )
        await engine.wait_for_inference()
        assert engine.state is GameState.AWAITING_PLAYERS
        await engine.process_payload("host", payload("start_game"))
        await engine.wait_for_inference()
        await engine.process_payload("host", payload("action", action="Wait"))
        await engine.wait_for_inference()
        assert engine.round_counter == 1 and not engine.round_paused
        assert not sender.events_of_type("error")
        content = engine.transcript.path.read_text(encoding="utf-8")
        assert "<dt>Chance</dt><dd>0%</dd>" in content
        assert spec.eligibility in content and spec.effect in content
        if spec.trigger:
            assert spec.trigger in content
        assert structured_rule_text(spec) not in content.split("</header>", 1)[0]
        await engine.shutdown()

    asyncio.run(run())
