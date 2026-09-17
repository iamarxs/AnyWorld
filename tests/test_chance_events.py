"""Exact event probabilities, private planning and retry-safe round integration."""

import asyncio
import json

import pytest
from pydantic import ValidationError

from core.config import settings
from core.schemas import ChanceEvent, ChanceEventResult, DicePlan, RoundResolution, SummaryAudit
from logic import dice
from logic.llm_manager import LLMContextManager, LLMResolutionError, participant_schema
from test_engine import payload
from test_priority_one_lifecycle import setup, submit_round
from test_priority_one_llm import FakeClient

RULE = "Add a 20% chance every time a building is entered that it collapses on the player."


def event(**changes):
    """One conditional occurrence, grounded in a private rule."""
    return ChanceEvent.model_validate(
        {
            "source_rule": RULE,
            "trigger": "condition",
            "occurrence": "Host enters the <tower>",
            "chance_percent": 20,
            **changes,
        }
    )


@pytest.mark.parametrize("percentage", [0, 2, 20, 99, 100])
def test_exact_probability_over_all_possible_draws(monkeypatch, percentage):
    """Exactly p of the 100 equiprobable draws trigger a p-percent event."""
    draws = iter(range(100))

    def draw(upper):
        assert upper == 100
        return next(draws)

    monkeypatch.setattr(dice.secrets, "randbelow", draw)
    results = [dice.roll_chance(event(chance_percent=percentage)) for _ in range(100)]
    assert sum(result.occurred for result in results) == percentage
    assert [result.roll for result in results] == list(range(1, 101))


@pytest.mark.parametrize("percentage", [-1, 101, 2.5, "20", True])
def test_invalid_probability_rejected(percentage):
    with pytest.raises(ValidationError):
        event(chance_percent=percentage)


@pytest.mark.parametrize(
    "events,guidance",
    [
        ([event()], ""),
        ([event(chance_percent=21)], RULE),
        ([event(), event()], RULE),
        ([event(source_rule="20%")], "No percentages in private guidance."),
        ([event(occurrence="   ")], RULE),
        ([event(source_rule="20.5% chance", chance_percent=20)], "20.5% chance"),
        ([event(source_rule="20% or 30% chance")], "20% or 30% chance"),
    ],
)
def test_invalid_event_plan_rejected(events, guidance):
    with pytest.raises(ValueError):
        dice.validate_chance_events(events, guidance)


def test_distinct_occurrences_and_every_round_rule_are_allowed():
    daily = event(
        source_rule="Add a 2% chance every round that a stranger helps the party.",
        trigger="per_round",
        occurrence="round",
        chance_percent=2,
    )
    dice.validate_chance_events(
        [daily, event(), event(occurrence="Player enters the barn")],
        daily.source_rule + "\n" + RULE,
    )
    schema = participant_schema(DicePlan, ("Host",), False).model_json_schema()
    assert schema["properties"]["chance_events"]["maxItems"] == 0


@pytest.mark.parametrize(
    "reference",
    [
        "rule-1",
        "Each round, one player's clothes turn into a clown costume (50% chance).",
        "One player's clothes turn into a clown costume",
    ],
)
def test_reported_guidance_accepts_ids_and_paraphrases_without_exact_round_label(reference):
    rule = (
        "Include a 50% chance one of the players have their clothes inexplicably "
        "turned into a clown costume."
    )
    guidance = rule + "\nIntroduce new NPCs when the story looks like it's stagnating."
    planned = event(
        source_rule=reference, chance_percent=50, trigger="per_round", occurrence="every round"
    )
    resolved = dice.validate_chance_events([planned], guidance)
    assert dice.private_chance_rules(guidance) == {"rule-1": (rule, 50)}
    assert resolved[0].source_rule == rule
    assert resolved[0].occurrence == "round"
    assert resolved[0].chance_percent == 50
    assert planned.source_rule == reference


def test_equal_percentages_use_rule_ids_and_reject_ambiguous_paraphrases():
    other = "Add a 20% chance every round that a helpful stranger appears."
    guidance = RULE + "\n" + other
    resolved = dice.validate_chance_events([event(source_rule="rule-2")], guidance)
    assert resolved[0].source_rule == other
    with pytest.raises(ValueError, match="rule ID"):
        dice.validate_chance_events([event(source_rule="Something happens")], guidance)
    with pytest.raises(ValueError, match="repeat"):
        dice.validate_chance_events([event(source_rule="rule-1"), event()], guidance)


def test_planner_catalog_resolves_rule_reference_before_returning():
    async def run():
        settings.llm.context_window_size = 32_768
        client = FakeClient(
            DicePlan(
                rolls={"Host": False}, hidden_rolls=[], chance_events=[event(source_rule="rule-1")]
            )
        )
        manager = LLMContextManager(client)
        manager.set_genesis("A tower", RULE + "\nIntroduce NPCs if the story stalls.")
        plan = await manager.plan_dice({"Host": "Enter the tower"})
        assert plan.chance_events == [event()]
        prompt = client.calls[0]["messages"][-1]["content"]
        assert '"source_rule": "rule-1"' in prompt
        assert RULE in prompt
        assert len(client.calls) == 1
        await manager.close()

    asyncio.run(run())


def test_planner_validates_private_source_and_resolution_receives_authoritative_result():
    async def run():
        settings.llm.context_window_size = 32_768
        client = FakeClient(
            DicePlan(rolls={"Host": False}, hidden_rolls=[], chance_events=[event()])
        )
        manager = LLMContextManager(client)
        manager.set_genesis("A tower", RULE)
        plan = await manager.plan_dice({"Host": "Enter the tower"})
        assert plan.chance_events == [event()]
        planner_prompt = client.calls[0]["messages"][-1]["content"]
        assert "entering is not remaining inside" in planner_prompt
        result = ChanceEventResult(event=event(), roll=21, occurred=False)
        client.result = RoundResolution(
            global_narrative="The tower stands intact.",
            player_resolutions={"Host": "Host enters the tower safely."},
        )
        await manager.generate_resolution({"Host": "Enter the tower"}, chance_events=[result])
        prompt = client.calls[-1]["messages"][-1]["content"]
        assert '"occurred": false' in prompt
        assert "not action-quality dice" in prompt
        assert "trigger actually occurs" in prompt
        assert len(client.calls) == 2
        await manager.close()

    asyncio.run(run())


def test_planner_cannot_invent_probability_from_player_action():
    async def run():
        settings.llm.context_window_size = 32_768
        settings.llm.max_retries = 0
        client = FakeClient(
            DicePlan(rolls={"Host": False}, hidden_rolls=[], chance_events=[event()])
        )
        manager = LLMContextManager(client)
        manager.set_genesis("A tower", "Keep the surroundings peaceful.")
        with pytest.raises(LLMResolutionError, match="private whole-percent"):
            await manager.plan_dice({"Host": RULE})
        assert manager.history == []
        await manager.close()

    asyncio.run(run())


@pytest.mark.parametrize("leak", ["The hidden check rolled 19.", "The collapse had a 20% chance."])
def test_private_event_mechanics_rejected_before_remembering(leak):
    async def run():
        settings.llm.context_window_size = 32_768
        settings.llm.max_retries = 0
        client = FakeClient(
            RoundResolution(global_narrative=leak, player_resolutions={"Host": "Host enters."})
        )
        manager = LLMContextManager(client)
        manager.set_genesis("A tower", RULE)
        with pytest.raises(LLMResolutionError, match="private"):
            await manager.generate_resolution(
                {"Host": "Enter"},
                chance_events=[ChanceEventResult(event=event(), roll=19, occurred=True)],
            )
        assert manager.history == []
        await manager.close()

    asyncio.run(run())


@pytest.mark.parametrize("repair_succeeds", [False, True])
def test_successful_event_omission_is_repaired_or_paused_before_remembering(repair_succeeds):
    """A successful roll cannot be committed solely because the round JSON is valid."""

    async def run():
        settings.llm.context_window_size = 32_768
        settings.llm.max_retries = 1
        rule = "Include a 50% chance one player's clothes turn into a clown costume every round."
        check = ChanceEventResult(
            event=event(
                source_rule=rule, chance_percent=50, trigger="per_round", occurrence="round"
            ),
            roll=30,
            occurred=True,
        )
        drafts = []
        audits = []

        def response(kwargs):
            if kwargs["response_format"] is SummaryAudit:
                audits.append(kwargs)
                return SummaryAudit(
                    preserved=repair_succeeds and len(audits) == 2,
                    corrections=[
                        "Host's clothes must visibly become a clown costume in both fields."
                    ],
                )
            text = (
                "Host's clothes become a clown costume."
                if repair_succeeds and drafts
                else "Host watches the gate."
            )
            draft = RoundResolution(global_narrative=text, player_resolutions={"Host": text})
            drafts.append(draft)
            return draft

        client = FakeClient(response)
        manager = LLMContextManager(client)
        manager.set_genesis("A gate", rule)
        if repair_succeeds:
            result = await manager.generate_resolution({"Host": "Watch"}, chance_events=[check])
            assert "clown costume" in result.global_narrative
            assert json.loads(manager.history[-1]["content"]) == result.model_dump()
            assert len(manager.history) == 2
            assert "Host watches the gate." not in manager.history[-1]["content"]
        else:
            with pytest.raises(LLMResolutionError, match="consequences were omitted"):
                await manager.generate_resolution({"Host": "Watch"}, chance_events=[check])
            assert manager.history == []
        assert len(drafts) == len(audits) == 2
        assert manager.game_usage.attempts == 4
        for call in client.calls:
            text = " ".join(message["content"] for message in call["messages"])
            assert '"roll": 30' in text and '"occurred": true' in text
        assert client.calls[1]["max_completion_tokens"] == settings.llm.summary_output_tokens
        assert "same event results" in client.calls[2]["messages"][-1]["content"]
        await manager.close()

    asyncio.run(run())


@pytest.mark.parametrize("per_round", [False, True])
def test_round_retries_reuse_events_and_only_private_archives_expose_them(
    tmp_path, monkeypatch, caplog, per_round
):
    caplog.set_level("INFO", logger="logic.engine")

    async def run():
        engine, sender, resolver = await setup(tmp_path)
        check = (
            event(
                source_rule="Add a 20% chance every round that a stranger appears.",
                trigger="per_round",
                occurrence="round",
            )
            if per_round
            else event()
        )
        engine.private_guidance = check.source_rule
        plans = []
        received = []
        draws = []

        async def plan(actions, current_state=""):
            plans.append(actions)
            # A second round with no new entry has no conditional check.
            return DicePlan(
                rolls={name: False for name in actions},
                hidden_rolls=[],
                chance_events=[check] if per_round or len(plans) == 1 else [],
            )

        async def resolve(actions, dice_results=None, hidden_rolls=None, chance_events=None):
            received.append(chance_events)
            if len(received) == 1:
                raise LLMResolutionError("Temporary failure")
            return RoundResolution(
                global_narrative="The tower falls.",
                player_resolutions={name: f"{name} sees falling stones." for name in actions},
            )

        def draw(upper):
            draws.append(upper)
            return 19

        monkeypatch.setattr(resolver, "plan_dice", plan)
        monkeypatch.setattr(resolver, "generate_resolution", resolve)
        monkeypatch.setattr(dice.secrets, "randbelow", draw)
        await submit_round(engine)
        await engine.wait_for_inference()
        assert engine.round_paused
        await engine.process_payload("host", payload("retry_round"))
        await engine.wait_for_inference()
        assert engine.round_counter == 1
        assert received[0] == received[1]
        assert received[1][0].occurred
        assert len(plans) == 1 and draws == [100]
        logs = [
            record.getMessage()
            for record in caplog.records
            if "Private chance events" in record.getMessage()
        ]
        assert len(logs) == 2
        assert "reused=False" in logs[0] and "reused=True" in logs[1]
        for line in logs:
            assert check.source_rule in line
            assert '"chance_percent": 20' in line
            assert '"roll": 20' in line and '"occurred": true' in line
        public = json.dumps([message.model_dump() for _, message in sender.events])
        assert "chance_events" not in public and check.source_rule not in public
        assert "chance_percent" not in public and "source_rule" not in public
        archive = engine.transcript.path.read_text(encoding="utf-8")
        assert "Private percentage events" in archive
        if not per_round:
            assert "Host enters the &lt;tower&gt;" in archive
        assert "20% chance; roll 20/100; triggered" in archive
        await submit_round(engine)
        await engine.wait_for_inference()
        assert engine.round_counter == 2 and len(plans) == 2
        if per_round:
            assert received[-1][0].event == check and draws == [100, 100]
        else:
            assert received[-1] is None and draws == [100]
        await engine.shutdown()

    asyncio.run(run())
