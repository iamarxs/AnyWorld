"""Semantic contracts, exact request reuse and stable validated history."""

import asyncio
import json

import httpx
import pytest

from core.config import settings
from core.schemas import DicePlan, RoundResolution, SummaryAudit
from logic.llm_manager import LLMContextManager, LLMResolutionError, participant_schema
from test_priority_one_llm import FakeClient, memory


@pytest.mark.parametrize(
    "result",
    [
        DicePlan(rolls={"Other": True}, hidden_rolls=[]),
        DicePlan(rolls={"Alice": False}, hidden_rolls=["Alice"]),
        DicePlan(rolls={"Alice": True}, hidden_rolls=["Alice", "Alice"]),
    ],
)
def test_bad_dice_is_repaired_once_then_rejected(result):
    """Bad player sets or hidden membership never reach the engine as a valid plan."""

    async def run():
        client = FakeClient(result)
        manager = LLMContextManager(client)
        with pytest.raises(LLMResolutionError):
            await manager.plan_dice({"Alice": "Wait"})
        assert len(client.calls) == 2
        assert manager.history == []
        assert manager.game_usage.retries == 1

    asyncio.run(run())


@pytest.mark.parametrize(
    "result",
    [
        RoundResolution(global_narrative="", player_resolutions={"Alice": "Waits"}),
        RoundResolution(global_narrative="A gate", player_resolutions={"Alice": " "}),
        RoundResolution(global_narrative="A gate", player_resolutions={"Other": "Waits"}),
    ],
)
def test_invalid_resolution_never_enters_history(result):
    """Repair failures preserve the original conversation."""

    async def run():
        manager = LLMContextManager(FakeClient(result))
        original = [{"role": "user", "content": "An existing fact"}]
        manager.history = list(original)
        with pytest.raises(LLMResolutionError):
            await manager.generate_resolution({"Alice": "Wait"}, {"Alice": 50})
        assert manager.history == original

    asyncio.run(run())


def test_initial_title_is_required_and_opening_has_no_outcomes():
    """Opening contracts are checked before remembering setup."""

    async def run():
        manager = LLMContextManager(
            FakeClient(
                RoundResolution(round_title=" ", global_narrative="A gate", player_resolutions={})
            )
        )
        with pytest.raises(LLMResolutionError):
            await manager.generate_initial_state()
        assert not manager.history
        schema = participant_schema(RoundResolution, ()).model_json_schema()
        assert schema["properties"]["player_resolutions"]["additionalProperties"] is False

    asyncio.run(run())


def test_validated_raw_response_retains_spacing_but_normalization_is_consistent():
    """Keep the exact generated prefix when safe; normalize once before remembering."""

    async def run():
        client = FakeClient()
        original_parse = client.parse

        async def parse(**kwargs):
            response = await original_parse(**kwargs)
            response.choices[0].message.content = json.dumps(
                response.choices[0].message.parsed.model_dump(), indent=2
            )
            return response

        client.beta.chat.completions.parse = parse
        manager = LLMContextManager(client)
        result = await manager.generate_resolution({"Alice": "Wait"})
        assert manager.history[-1]["content"] == json.dumps(result.model_dump(), indent=2)
        client.result = RoundResolution(
            global_narrative="A gate", player_resolutions={"Alice": "He waits."}
        )
        result = await manager.generate_resolution({"Alice": "Wait"})
        assert result.player_resolutions["Alice"] == "Alice waits."
        assert json.loads(manager.history[-1]["content"]) == result.model_dump()

    asyncio.run(run())


def test_request_token_cache_is_bounded_and_invalidates_on_input_and_template_change():
    """Repeated requests skip HTTP; appended input and changed template options recount."""

    async def run():
        settings.llm.provider = "compatible"
        manager = LLMContextManager(FakeClient())
        calls = []

        def backend(request):
            calls.append(request.url.path)
            if request.url.path == "/apply-template":
                return httpx.Response(200, json={"prompt": "Formatted request"})
            return httpx.Response(200, json={"tokens": [1, 2, 3]})

        manager._http = httpx.AsyncClient(transport=httpx.MockTransport(backend))
        messages = [{"role": "user", "content": "A fact"}]
        count = await manager._input_tokens(messages, RoundResolution)
        assert await manager._input_tokens(messages, RoundResolution) == count
        assert len(calls) == 2
        settings.llm.enable_thinking = False
        await manager._input_tokens(messages, RoundResolution)
        assert len(calls) == 4
        for index in range(140):
            await manager._input_tokens(
                [*messages, {"role": "user", "content": str(index)}], RoundResolution
            )
        assert len(manager._request_counts) == 128
        manager.set_genesis("New session")
        assert not manager._request_counts
        await manager.close()

    asyncio.run(run())


def test_message_cache_matches_fresh_counts_and_stays_bounded():
    """Append/reset cannot introduce stale counts or an unbounded text cache."""
    manager = LLMContextManager(FakeClient())
    history = [{"role": "user", "content": "日本語"}, {"role": "assistant", "content": "A gate"}]
    expected = 32 + sum(len(item["content"].encode("utf-8")) + 32 for item in history)
    assert manager._context_size(history) == expected
    for index in range(600):
        manager._count_tokens(str(index))
    assert len(manager._text_counts) == 512
    assert manager._context_size(history) == expected


def test_rejected_summary_audit_preserves_original_context():
    """A failed memory audit spends tokens but commits neither memory nor history."""

    async def run():
        def result(kwargs):
            if kwargs["response_format"] is SummaryAudit:
                return SummaryAudit(
                    preserved=False, corrections=["An established resource was omitted."]
                )
            return memory()

        manager = LLMContextManager(FakeClient(result))
        manager.history = [
            {"role": "user", "content": "Alice examines the gate. " * 100},
            {"role": "assistant", "content": memory().model_dump_json() + " detail" * 140},
        ]
        original = manager.history
        with pytest.raises(LLMResolutionError, match="durable facts"):
            await manager._request(
                {"role": "user", "content": "Wait. " * 350}, RoundResolution, remember=True
            )
        assert manager.history is original
        assert manager.memory is None
        assert manager.game_usage.attempts == 2

    asyncio.run(run())
