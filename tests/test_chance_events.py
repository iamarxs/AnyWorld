"""Exact event probabilities, private planning and retry-safe round integration."""

import asyncio
import json

import pytest
from pydantic import ValidationError

from core.config import settings
from core.schemas import (
    AuditVerdict,
    ChanceEvent,
    ChanceEventResult,
    ChanceRuleDecision,
    ChanceRuleInterpretation,
    ChanceTriggerPlan,
    DicePlan,
    RoundResolution,
)
from logic import dice
from logic.llm.validation import normalize_hidden_roll_sources
from logic.llm_manager import LLMContextManager, LLMResolutionError, participant_schema
from test_engine import payload
from test_priority_one_lifecycle import setup, submit_round
from test_priority_one_llm import FakeClient

RULE = "Add a 20% chance every time a building is entered that it collapses on the player."
FIREBALL_RULE = (
    "Add a 100% chance that if a player casts a fireball, they lose the ability to speak "
    "and understand language for a short duration."
)


def conditional_client(plan, occurrences):
    """Return a fake client for the normalization and trigger passes."""
    interpretation = ChanceRuleInterpretation(
        trigger_type="action",
        trigger_description="The player performs the conditional action.",
        occurrence_scope="per_player",
        effect="The private chance effect occurs.",
    )

    def response(kwargs):
        if kwargs["response_format"] is ChanceRuleInterpretation:
            return interpretation
        if kwargs["response_format"] is ChanceTriggerPlan:
            return ChanceTriggerPlan(occurrences=occurrences)
        return plan

    return FakeClient(response)


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
    "guidance,chance_event,match",
    [
        ("Keep the tone eerie.", "Add a 40% chance every round that a bell rings.", None),
        ("Add a 20% chance every round that a bell rings.", "", "Freeform"),
        ("Add a forty percent chance that a bell rings.", "", "Freeform"),
        ("There is a 40 per cent chance a bell rings.", "", "Freeform"),
        ("Use the phrase percentage chance in the narration.", "", "Freeform"),
        (
            "Keep the tone eerie.",
            "Add a 20% chance and a 30% chance that a bell rings.",
            "exactly one",
        ),
        (
            "Keep the tone eerie.",
            "Add a 20.5% chance that a bell rings.",
            "whole-number",
        ),
        ("Keep the tone eerie.", "Add a 20% chance\nthat a bell rings.", "single line"),
    ],
)
def test_scenario_guidance_accepts_at_most_one_dedicated_chance_event(
    guidance, chance_event, match
):
    if match:
        with pytest.raises(ValueError, match=match):
            dice.combine_private_guidance(guidance, chance_event)
    else:
        assert dice.combine_private_guidance(guidance, chance_event) == (
            guidance + "\n" + chance_event
        )


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


def test_multiple_percentage_rules_are_rejected_but_freeform_guidance_is_unchanged():
    with pytest.raises(ValueError, match="Only one"):
        dice.private_chance_rule(
            RULE + "\nAdd a 40% chance every round that a stranger helps the party."
        )
    guidance = "Keep the tone eerie.\nIntroduce new NPCs when the story stalls."
    assert dice.combine_private_guidance(guidance, RULE) == guidance + "\n" + RULE
    schema = participant_schema(DicePlan, ("Host",), False).model_json_schema()
    assert schema["properties"]["chance_events"]["maxItems"] == 0
    assert "chance_rule_decisions" not in schema["properties"]


@pytest.mark.parametrize(
    "reference",
    [
        "Each round, one player's clothes turn into a clown costume (50% chance).",
        "One player's clothes turn into a clown costume",
    ],
)
def test_reported_guidance_accepts_single_rule_paraphrases(reference):
    rule = (
        "Include a 50% chance one of the players have their clothes inexplicably "
        "turned into a clown costume."
    )
    guidance = rule + "\nIntroduce new NPCs when the story looks like it's stagnating."
    planned = event(
        source_rule=reference, chance_percent=50, trigger="per_round", occurrence="every round"
    )
    resolved = dice.validate_chance_events([planned], guidance)
    assert dice.private_chance_rule(guidance) == (rule, 50)
    assert resolved[0].source_rule == rule
    assert resolved[0].occurrence == "round"
    assert resolved[0].chance_percent == 50
    assert planned.source_rule == reference


def test_planner_builds_per_round_event_without_a_chance_decision_field():
    async def run():
        settings.llm.context_window_size = 32_768
        rule = "Add a 2% chance every round that a bell rings."
        client = FakeClient(
            DicePlan(
                rolls={"Host": False},
                hidden_rolls=[],
            )
        )
        manager = LLMContextManager(client)
        manager.set_genesis("A tower", rule + "\nIntroduce NPCs if the story stalls.")
        plan = await manager.plan_dice({"Host": "Enter the tower"})
        assert plan.chance_events[0].source_rule == rule
        assert plan.chance_events[0].trigger == "per_round"
        assert len(client.calls) == 1
        schema = client.calls[0]["response_format"].model_json_schema()
        assert "chance_rule_decisions" not in schema["properties"]
        await manager.close()

    asyncio.run(run())


@pytest.mark.parametrize(
    "action,expected_event_count",
    [
        ("Confront the Void and mend its longing.", 0),
        ("Attempt to cast a fireball to light the ruins.", 1),
        ("I cannot cast a fireball here.", 0),
    ],
)
def test_concrete_action_chance_rules_are_matched_without_an_occurrence_audit(
    action, expected_event_count
):
    """Specific action triggers cannot be hallucinated or rejected by paraphrase audits."""

    async def run():
        settings.llm.context_window_size = 32_768
        settings.llm.max_retries = 1

        def response(kwargs):
            if kwargs["response_format"] is ChanceRuleInterpretation:
                return ChanceRuleInterpretation(
                    trigger_type="action",
                    trigger_description="A player casts a fireball.",
                    occurrence_scope="per_player",
                    effect="The caster loses language comprehension temporarily.",
                )
            if kwargs["response_format"] is ChanceTriggerPlan:
                return ChanceTriggerPlan(
                    occurrences=(
                        ["Arxs"]
                        if "fireball" in action and "cannot" not in action
                        else ["not-a-player"]
                    )
                )
            return DicePlan(
                rolls={"Arxs": False},
                hidden_rolls=[],
            )

        client = FakeClient(response)
        manager = LLMContextManager(client)
        manager.set_genesis("A ruined temple", FIREBALL_RULE)
        await manager.prepare_chance_rule()

        plan = await manager.plan_dice({"Arxs": action})

        assert len(plan.chance_events) == expected_event_count
        assert len(client.calls) == 3
        assert (
            "chance_rule_decisions"
            not in client.calls[2]["response_format"].model_json_schema()["properties"]
        )
        assert "only exact current player names" in client.calls[1]["messages"][-1]["content"]
        assert (
            "separate authoritative structured pass" in client.calls[2]["messages"][-1]["content"]
        )
        await manager.close()

    asyncio.run(run())


def test_planner_validates_private_source_and_resolution_receives_authoritative_result():
    async def run():
        settings.llm.context_window_size = 32_768
        client = conditional_client(DicePlan(rolls={"Host": False}, hidden_rolls=[]), ["Host"])
        manager = LLMContextManager(client)
        manager.set_genesis("A tower", RULE)
        plan = await manager.plan_dice({"Host": "Enter the tower"})
        assert len(plan.chance_events) == 1
        assert plan.chance_events[0].source_rule == RULE
        planner_prompt = client.calls[1]["messages"][-1]["content"]
        assert "normalized private chance rule" in planner_prompt
        result = ChanceEventResult(event=event(), roll=21, occurred=False)
        client.result = RoundResolution(
            global_narrative="The tower stands intact.",
            player_resolutions={"Host": "Host enters the tower safely."},
        )
        await manager.generate_resolution({"Host": "Enter the tower"}, chance_events=[result])
        prompt = client.calls[-1]["messages"][-1]["content"]
        assert '"occurred": false' in prompt
        assert "not action-quality dice" in prompt
        assert "conditional events apply when their condition occurs" in prompt
        assert len(client.calls) == 4
        await manager.close()

    asyncio.run(run())


def test_planner_cannot_invent_probability_from_player_action():
    async def run():
        settings.llm.context_window_size = 32_768
        settings.llm.max_retries = 0
        client = FakeClient(DicePlan(rolls={"Host": False}, hidden_rolls=[]))
        manager = LLMContextManager(client)
        manager.set_genesis("A tower", "Keep the surroundings peaceful.")
        plan = await manager.plan_dice({"Host": RULE})
        assert plan.chance_events == []
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


def test_single_conditional_rule_preserves_multiple_player_occurrences():
    async def run():
        settings.llm.context_window_size = 32_768
        manager = LLMContextManager(
            conditional_client(
                DicePlan(rolls={"Host": False, "Player": False}, hidden_rolls=[]),
                ["Host", "Player"],
            )
        )
        manager.set_genesis("Two buildings", RULE)
        plan = await manager.plan_dice({"Host": "Enter the tower", "Player": "Enter the tower"})
        assert [item.source_rule for item in plan.chance_events] == [RULE, RULE]
        assert len(plan.chance_events) == 2
        assert all("normalized trigger" in item.occurrence for item in plan.chance_events)
        await manager.close()

    asyncio.run(run())


def test_occurrences_are_the_single_source_of_chance_rolls():
    decision = ChanceRuleDecision(trigger="condition", occurrences=[event().occurrence])
    assert dice.chance_events_from_decision(decision, RULE) == [event()]
    decision.occurrences = []
    assert dice.chance_events_from_decision(decision, RULE) == []


def test_conditional_round_occurrence_is_rejected_instead_of_rolled():
    """A conditional rule cannot become active merely because a round occurred."""
    decision = ChanceRuleDecision(trigger="condition", occurrences=["round"])

    with pytest.raises(ValueError, match="reserved for per-round"):
        dice.chance_events_from_decision(decision, RULE)


def test_per_round_chance_events_do_not_require_a_model_decision():
    guidance = "Add a 40% chance every round that a Banana Split appears from nowhere."
    assert dice.chance_events_from_decision(None, guidance) == [
        ChanceEvent(
            source_rule=guidance,
            trigger="per_round",
            occurrence="round",
            chance_percent=40,
        )
    ]


def test_unspecified_trigger_per_round_decision_keeps_absurd_action_roll():
    async def run():
        rule = (
            "Add a 40% chance one of the players suddenly gets handed a Banana Split from nowhere"
        )
        client = FakeClient(
            DicePlan(
                rolls={"Arxs": True},
                hidden_rolls=[],
            )
        )
        manager = LLMContextManager(client)
        manager.set_genesis("A modern city.", rule)
        plan = await manager.plan_dice({"Arxs": "Manifest a company of Ultramarines."})
        assert plan.rolls == {"Arxs": True} and plan.hidden_rolls == []
        assert len(plan.chance_events) == 1
        assert plan.chance_events[0].trigger == "per_round"
        assert len(client.calls) == 1
        await manager.close()

    asyncio.run(run())


def test_conditional_chance_trigger_cannot_be_reclassified_as_per_round():
    decision = ChanceRuleDecision(
        trigger="per_round",
        occurrences=[event().occurrence],
    )

    normalized = dice.normalize_chance_rule_decision(decision, RULE)
    events = dice.chance_events_from_decision(normalized, RULE)

    assert normalized.trigger == "condition"
    assert events == [event()]


def test_chance_rule_without_explicit_trigger_defaults_to_per_round():
    rule = "Add a 40% chance one of the players suddenly gets handed a Banana Split from nowhere."
    decision = ChanceRuleDecision(trigger="condition", occurrences=[])

    events = dice.chance_events_from_decision(decision, rule)

    assert events == [
        ChanceEvent(
            source_rule=rule,
            trigger="per_round",
            occurrence="round",
            chance_percent=40,
        )
    ]


def test_per_round_plan_needs_no_audit_and_invalid_hidden_source_stays_public():
    async def run():
        guidance = "Introduce strange visitors when the story stalls."
        chance = "Add a 40% chance per round that a Banana Split appears."
        drafts = []

        def response(kwargs):
            assert kwargs["response_format"] is not AuditVerdict
            drafts.append(kwargs)
            return DicePlan(
                rolls={"Arxs": True},
                hidden_rolls=["Arxs"] if len(drafts) == 1 else [],
                hidden_roll_sources={"Arxs": chance} if len(drafts) == 1 else {},
            )

        manager = LLMContextManager(FakeClient(response))
        manager.set_genesis("A modern city with reality warps.", guidance + "\n" + chance)
        plan = await manager.plan_dice({"Arxs": "Use a Vox-Caster to contact the Inquisition."})
        assert plan.rolls == {"Arxs": True} and plan.hidden_rolls == []
        assert len(plan.chance_events) == 1
        assert plan.chance_events[0].chance_percent == 40
        assert len(drafts) == 1
        schema = drafts[0]["response_format"].model_json_schema()
        sources = schema["properties"]["hidden_roll_sources"]["properties"]["Arxs"]["enum"]
        assert sources == ["", guidance]
        await manager.close()

    asyncio.run(run())


def test_explicit_per_turn_rules_are_authoritative_when_model_omits_occurrences():
    """A model cannot suppress unconditional percentage events by misclassifying them."""
    guidance = "Add a 40% chance per turn that dwarves intervene."
    events = dice.chance_events_from_decision(None, guidance)
    assert [(event.trigger, event.occurrence, event.chance_percent) for event in events] == [
        ("per_round", "round", 40)
    ]


def test_percentage_only_guidance_cannot_enable_hidden_action_rolls():
    assert not dice.has_non_percentage_private_guidance(RULE)
    assert dice.has_non_percentage_private_guidance(RULE + "\nThe gate has an invisible alarm.")


def test_planner_keeps_freeform_steering_general_and_absurd_checks_public():
    async def run():
        client = FakeClient(DicePlan(rolls={"Host": True}, hidden_rolls=[]))
        manager = LLMContextManager(client)
        manager.set_genesis(
            "A modern city.",
            "Keep the world coherent while introducing surprising but compatible details.",
        )
        await manager.plan_dice({"Host": "Find the nearest base of the Human Imperium."})
        prompt = "\n".join(message["content"] for message in client.calls[0]["messages"])
        assert "setting-conflicting attempt" in prompt
        assert "freeform steering or a percentage event" in prompt
        assert "style, tone, diction" not in prompt
        await manager.close()

    asyncio.run(run())


def test_unrelated_private_guidance_cannot_hide_public_action_roll(tmp_path, monkeypatch):
    """Correct the plan before rolls are classified in public events and HTML."""

    async def run():
        engine, sender, resolver = await setup(tmp_path)
        guidance = "Introduce new NPCs when the plot seems to stagnate."
        engine.private_guidance = guidance
        drafts = []

        def response(kwargs):
            if kwargs["response_format"] is AuditVerdict:
                return AuditVerdict(
                    preserved=False,
                    corrections=["NPC pacing guidance does not make Host's ordinary roll private."],
                )
            drafts.append(kwargs)
            return DicePlan(
                rolls={"Host": True, "Player": False},
                hidden_rolls=["Host"] if len(drafts) == 1 else [],
                hidden_roll_sources={"Host": guidance} if len(drafts) == 1 else {},
            )

        manager = LLMContextManager(FakeClient(response))
        manager.set_genesis("A locked gate", guidance)
        monkeypatch.setattr(resolver, "plan_dice", manager.plan_dice)
        monkeypatch.setattr("logic.engine.roll_d100", lambda: 79)
        await submit_round(engine)
        await engine.wait_for_inference()
        assert not engine.round_paused and engine.round_counter == 1
        assert len(drafts) == 1
        assert sender.events_of_type("state_update")[-1].payload["dice_results"] == {"Host": 79}
        archive = engine.transcript.path.read_text(encoding="utf-8")
        assert "Host: 79/100 (resounding success)" in archive
        assert "Private checks from DM guidance" not in archive
        await manager.close()
        await engine.shutdown()

    asyncio.run(run())


@pytest.mark.parametrize("provider", ["compatible", "openai"])
@pytest.mark.parametrize("source", ["", RULE, "Keep the story moving quickly."])
def test_stray_hidden_inventory_label_preserves_no_roll_without_retry(provider, source):
    async def run():
        import httpx

        settings.llm.provider = provider
        draft = DicePlan(
            rolls={"Arxs": False, "Host": True},
            hidden_rolls=["Arxs"],
            hidden_roll_sources={"Arxs": source},
        )
        client = FakeClient(draft)
        manager = LLMContextManager(client)
        manager.budget._http = httpx.AsyncClient(
            transport=httpx.MockTransport(lambda request: httpx.Response(404))
        )
        manager.set_genesis(
            "A quiet storeroom with a locked door.",
            "Add a 20% chance per round that a dwarf interrupts a player's action.\n"
            "Keep the story moving quickly.",
        )
        plan = await manager.plan_dice(
            {"Arxs": "Inspect my inventory", "Host": "Force the locked door open"}
        )
        assert plan.rolls == {"Arxs": False, "Host": True}
        assert plan.hidden_rolls == [] and plan.hidden_roll_sources == {}
        assert len(plan.chance_events) == 1
        assert len(client.calls) == 1
        assert manager.game_usage.retries == 0
        assert draft.hidden_rolls == ["Arxs"]
        await manager.close()

    asyncio.run(run())


def test_confused_hidden_audit_cannot_replan_public_rolls_or_per_round_events():
    async def run():
        guidance = 'Reply in caveman-style language only. Example: "Dwarf hit player."'
        chance = "Add a 40% chance per round that a Banana Split appears."
        audits = []

        def response(kwargs):
            if kwargs["response_format"] is AuditVerdict:
                audits.append(kwargs)
                return AuditVerdict(
                    preserved=False,
                    corrections=["Missing Banana Split check; make psychic powers hidden."],
                )
            return DicePlan(
                rolls={"Arxs": True},
                hidden_rolls=["Arxs"],
                hidden_roll_sources={"Arxs": guidance},
            )

        client = FakeClient(response)
        manager = LLMContextManager(client)
        manager.set_genesis("A modern city.", guidance + "\n" + chance)
        plan = await manager.plan_dice({"Arxs": "Manifest a world using psychic powers."})
        assert plan.rolls == {"Arxs": True}
        assert plan.hidden_rolls == [] and plan.hidden_roll_sources == {}
        assert len(plan.chance_events) == 1 and plan.chance_events[0].chance_percent == 40
        assert len(client.calls) == 2 and len(audits) == 1
        assert "Banana Split" not in str(audits[0]["messages"])
        await manager.close()

    asyncio.run(run())


@pytest.mark.parametrize("source", ["", RULE, "Invented hidden source that is not guidance."])
def test_unsupported_hidden_sources_keep_action_roll_public(source):
    manager = LLMContextManager(FakeClient())
    guidance = "The locked gate conceals a private alarm."
    manager.set_genesis("A gate", guidance + "\n" + RULE)
    plan = normalize_hidden_roll_sources(
        DicePlan(rolls={"Host": True}, hidden_rolls=["Host"], hidden_roll_sources={"Host": source}),
        guidance=manager.private_guidance,
    )
    assert plan.rolls == {"Host": True}
    assert plan.hidden_rolls == [] and plan.hidden_roll_sources == {}


def test_valid_private_source_keeps_its_check_hidden():
    manager = LLMContextManager(FakeClient())
    guidance = "The locked gate conceals a private alarm."
    manager.set_genesis("A gate", guidance)
    plan = normalize_hidden_roll_sources(
        DicePlan(
            rolls={"Host": True}, hidden_rolls=["Host"], hidden_roll_sources={"Host": guidance}
        ),
        guidance=manager.private_guidance,
    )
    assert plan.hidden_rolls == ["Host"]


@pytest.mark.parametrize("failed_attempts", [1, 2])
def test_single_rule_occurrences_survive_retry(tmp_path, monkeypatch, failed_attempts):
    async def run():
        engine, sender, resolver = await setup(tmp_path)
        engine.private_guidance = RULE
        checks = [
            event(),
            event(occurrence="Player enters the barn"),
        ]
        values = iter([0, 99])
        draws = []
        received = []

        def draw(upper):
            draws.append(upper)
            return next(values)

        async def plan(actions, current_state=""):
            return DicePlan(
                rolls={name: False for name in actions}, hidden_rolls=[], chance_events=checks
            )

        async def resolve(actions, dice_results=None, hidden_rolls=None, chance_events=None):
            received.append(chance_events)
            if len(received) <= failed_attempts:
                raise LLMResolutionError("Retry needed")
            return RoundResolution(
                global_narrative="The tower collapses around the intact barn.",
                player_resolutions={name: f"{name} watches the tower." for name in actions},
            )

        monkeypatch.setattr(dice.secrets, "randbelow", draw)
        monkeypatch.setattr(resolver, "plan_dice", plan)
        monkeypatch.setattr(resolver, "generate_resolution", resolve)
        await submit_round(engine)
        await engine.wait_for_inference()
        assert not engine.round_paused
        assert not sender.events_of_type("error")
        assert engine.round_counter == 1
        assert draws == [100, 100]
        assert len(received) == failed_attempts + 1
        assert all(item == received[0] for item in received)
        assert received[0] == received[1]
        assert [result.roll for result in received[1]] == [1, 100]
        assert [result.occurred for result in received[1]] == [True, False]
        archive = engine.transcript.path.read_text(encoding="utf-8")
        for roll in (1, 100):
            assert f"roll {roll}/100" in archive
        assert sender.events_of_type("state_update")[-1].payload["dice_results"] == {}
        await engine.shutdown()

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
            if kwargs["response_format"] is AuditVerdict:
                audits.append(kwargs)
                return AuditVerdict(
                    preserved=repair_succeeds and len(audits) == 2,
                    corrections=["Host's clothes must visibly become a clown costume."],
                )
            text = (
                "Host's clothes become a clown costume."
                if repair_succeeds and drafts
                else "Host watches the gate."
            )
            draft = RoundResolution(
                global_narrative="The gate remains locked.", player_resolutions={"Host": text}
            )
            drafts.append(draft)
            return draft

        client = FakeClient(response)
        manager = LLMContextManager(client)
        manager.set_genesis("A gate", rule)
        if repair_succeeds:
            result = await manager.generate_resolution({"Host": "Watch"}, chance_events=[check])
            assert "clown costume" in result.player_resolutions["Host"]
            assert result.global_narrative == "The gate remains locked."
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


def test_nonblocking_event_prompt_preserves_the_original_action():
    async def run():
        settings.llm.context_window_size = 32_768
        rule = "Include a 50% chance one player's clothes change every round."
        check = ChanceEventResult(
            event=event(
                source_rule=rule, chance_percent=50, trigger="per_round", occurrence="round"
            ),
            roll=1,
            occurred=True,
        )
        resolution = RoundResolution(
            global_narrative="The window reveals a moonlit courtyard.",
            player_resolutions={
                "Host": (
                    "Host's clothing changes into a velvet robe, and Host looks out the "
                    "window to see the courtyard."
                )
            },
        )

        def response(kwargs):
            return (
                AuditVerdict(preserved=True, corrections=[])
                if kwargs["response_format"] is AuditVerdict
                else resolution
            )

        client = FakeClient(response)
        manager = LLMContextManager(client)
        manager.set_genesis("A tower", rule)
        await manager.generate_resolution(
            {"Host": "Look outside the window"}, chance_events=[check]
        )
        prompt = client.calls[0]["messages"][-1]["content"]
        assert "Chance effects are modifiers, not replacements for player actions" in prompt
        assert "resolve the intended action alongside the effect" in prompt
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
            if len(received) <= 3:
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
        assert len(logs) == 4
        assert all(item == received[0] for item in received)
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
