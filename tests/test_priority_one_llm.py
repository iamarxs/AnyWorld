"""Budget, memory, and private planning regressions without a live model."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest

from core.config import settings
from core.schemas import ContextSummary, DicePlan, RoundResolution
from logic.llm_manager import LLMContextManager, LLMResolutionError


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
        if isinstance(result, Exception):
            raise result
        if result is None:
            schema = kwargs["response_format"]
            if schema is DicePlan:
                result = DicePlan(rolls={"Alice": True}, hidden_rolls=["Alice"])
            elif schema is ContextSummary:
                result = memory()
            else:
                result = RoundResolution(
                    round_title="The gate",
                    global_narrative="A breeze rises.",
                    player_resolutions={"Alice": "Alice waits."},
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


@pytest.mark.parametrize(
    "schema,kind",
    [
        (DicePlan, "dice"),
        (RoundResolution, "initial"),
        (RoundResolution, "round"),
        (ContextSummary, "summary"),
    ],
)
@pytest.mark.parametrize(
    "provider,cap_key",
    [
        ("openai", "max_completion_tokens"),
        ("compatible", "max_tokens"),
    ],
)
def test_every_request_caps_output_and_counts_backend_template(schema, kind, provider, cap_key):
    """Verify every request caps output and counts the backend template."""

    async def run():
        settings.llm.provider = provider
        client = FakeClient()
        manager = LLMContextManager(client)
        seen = []

        def backend(request):
            seen.append(request.url.path)
            if request.url.path == "/apply-template":
                return httpx.Response(200, json={"prompt": "MODEL TEMPLATE + multilingual 日本語"})
            if request.url.path == "/tokenize":
                assert json.loads(request.content)["add_special"] is True
                return httpx.Response(200, json={"tokens": list(range(1400))})
            raise AssertionError(request.url)

        manager._http = httpx.AsyncClient(transport=httpx.MockTransport(backend))
        await manager._parse([{"role": "user", "content": "Act"}], schema, kind)
        request = client.calls[0]
        assert request[cap_key] == getattr(settings.llm, f"{kind}_output_tokens")
        assert (
            "max_tokens" if cap_key == "max_completion_tokens" else "max_completion_tokens"
        ) not in request
        if provider == "compatible":
            assert seen == ["/apply-template", "/tokenize"]
            assert manager.token_count_method.startswith("backend")
        await manager.close()
        assert client.closed

    asyncio.run(run())


@pytest.mark.parametrize(
    "kind,schema",
    [
        ("dice", DicePlan),
        ("initial", RoundResolution),
        ("round", RoundResolution),
        ("summary", ContextSummary),
    ],
)
def test_schema_output_and_margin_can_reject_a_short_message(kind, schema):
    """Verify schema output and margin can reject a short message."""

    async def run():
        client = FakeClient()
        manager = LLMContextManager(client)
        manager.context_window_size = getattr(settings.llm, f"{kind}_output_tokens") + 256
        with pytest.raises(LLMResolutionError, match="budget"):
            await manager._parse([{"role": "user", "content": "x"}], schema, kind)
        assert not client.calls

    asyncio.run(run())


def test_unknown_backend_uses_utf8_bytes_and_keeps_small_slot_context():
    """Verify an unknown backend uses UTF-8 bytes and keeps a small slot context."""

    async def run():
        settings.llm.provider = "compatible"
        client = FakeClient()
        manager = LLMContextManager(client)

        def backend(request):
            if request.url.path == "/props":
                return httpx.Response(
                    200,
                    json={
                        "n_ctx": 131072,
                        "total_slots": 4,
                        "default_generation_settings": {"n_ctx": 4096},
                    },
                )
            return httpx.Response(404)

        manager._http = httpx.AsyncClient(transport=httpx.MockTransport(backend))
        await manager.discover_context_window()
        assert manager.context_window_size == 4096
        assert manager._count_tokens("💎日本語") == len("💎日本語".encode("utf-8"))
        with pytest.raises(LLMResolutionError):
            await manager._parse(
                [{"role": "user", "content": "💎" * 1000}], RoundResolution, "round"
            )
        assert manager.token_count_method == "conservative UTF-8 estimate"
        assert not client.calls
        await manager.close()

    asyncio.run(run())


def test_large_scenario_is_rejected_before_inference():
    """Verify a large scenario is rejected before inference."""

    async def run():
        client = FakeClient()
        manager = LLMContextManager(client)
        manager.set_genesis("城" * 20_000, "private" * 700)
        with pytest.raises(LLMResolutionError):
            await manager.generate_initial_state()
        assert not client.calls

    asyncio.run(run())


def test_aggregate_preflight_rejects_without_mutating_memory():
    """Verify an aggregate preflight rejects without mutating memory."""

    async def run():
        client = FakeClient()
        manager = LLMContextManager(client)
        manager.set_genesis("A gate")
        manager.memory = {"role": "user", "content": memory().model_dump_json()}
        before = manager.memory.copy()
        with pytest.raises(LLMResolutionError):
            await manager.preflight_round({str(i): "x" * 4000 for i in range(100)})
        assert manager.memory == before and not client.calls

    asyncio.run(run())


def test_large_next_action_triggers_summary_and_repeated_memory_survives():
    """Verify a large next action triggers a summary and repeated memory survives."""

    async def run():
        client = FakeClient()
        manager = LLMContextManager(client)
        manager.set_genesis("A gate. PRIVATE_TRIGGER is an invisible alarm.")
        for _ in range(3):
            manager.history = [
                {"role": "user", "content": "Alice examines the gate. " * 100},
                {"role": "assistant", "content": memory().model_dump_json() + " detail" * 140},
            ]
            await manager._request(
                {"role": "user", "content": "Wait. " * 350}, RoundResolution, remember=True
            )
            assert manager.memory is not None
            for fact in ["brass key", "Broken wrist", "vial was consumed", "Mira", "dusk"]:
                assert fact in manager.memory["content"]
        summaries = [c for c in client.calls if c["response_format"] is ContextSummary]
        assert len(summaries) == 3
        assert "Durable historical memory" in str(summaries[-1]["messages"])
        assert manager.genesis_state["content"].endswith("invisible alarm.")

    asyncio.run(run())


@pytest.mark.parametrize(
    "result",
    [
        ValueError("PRIVATE failure text"),
        ContextSummary(world_state="", player_states={}, important_npcs="", unresolved_threads=[]),
        ContextSummary(
            world_state="too long " * 1000,
            player_states={},
            important_npcs="",
            unresolved_threads=[],
        ),
    ],
)
def test_failed_empty_or_expanding_summary_preserves_original(result):
    """Verify a failed, empty or expanding summary preserves the original."""

    async def run():
        client = FakeClient(result)
        manager = LLMContextManager(client)
        manager.memory = {"role": "user", "content": memory().model_dump_json()}
        manager.history = [
            {"role": "user", "content": "earlier action " * 180},
            {"role": "assistant", "content": "earlier state " * 100},
        ]
        previous_memory, previous_history = manager.memory, manager.history
        with pytest.raises(LLMResolutionError):
            await manager._request(
                {"role": "user", "content": "next action " * 230}, RoundResolution, remember=True
            )
        assert manager.memory is previous_memory
        assert manager.history is previous_history

    asyncio.run(run())


def test_truncation_does_not_enter_history():
    """Verify truncation does not enter the history."""

    async def run():
        manager = LLMContextManager(FakeClient(finish_reason="length"))
        with pytest.raises(LLMResolutionError, match="token limit"):
            await manager.generate_initial_state()
        assert manager.history == []

    asyncio.run(run())


def test_planner_sees_private_guidance_durable_facts_and_recent_changes():
    """Verify the planner sees private guidance, durable facts and recent changes."""

    async def run():
        client = FakeClient()
        manager = LLMContextManager(client)
        manager.set_genesis("North gate", "PRIVATE_TRIGGER: invisible alarm at gate")
        manager.memory = {"role": "user", "content": memory().model_dump_json()}
        manager.history = [
            {"role": "user", "content": "Alice waits"},
            {"role": "assistant", "content": "Mira has left."},
        ]
        plan = await manager.plan_dice({"Alice": "open gate"}, "A breeze rises.")
        text = str(client.calls[-1]["messages"])
        for fact in [
            "PRIVATE_TRIGGER",
            "brass key",
            "Broken wrist",
            "gate remains locked",
            "Mira has left",
            "dusk",
        ]:
            assert fact in text
        assert len(manager.history) == 2
        await manager.generate_resolution(
            {"Alice": "open gate"}, {"Alice": 17}, hidden_rolls=set(plan.hidden_rolls)
        )
        assert "never disclose" in client.calls[-1]["messages"][-1]["content"]

    asyncio.run(run())


@pytest.mark.parametrize(
    "leak", ["Alice rolled 17 on a secret check.", "The invisible alarm triggers behind the gate."]
)
def test_public_output_leaking_secret_roll_or_guidance_is_rejected(leak):
    """Verify public output leaking a secret roll or guidance is rejected."""

    async def run():
        client = FakeClient(
            RoundResolution(global_narrative=leak, player_resolutions={"Alice": "Alice waits."})
        )
        manager = LLMContextManager(client)
        manager.set_genesis("North gate", "The invisible alarm triggers behind the gate.")
        with pytest.raises(LLMResolutionError, match="disclosed"):
            await manager.generate_resolution({"Alice": "wait"}, {"Alice": 17}, {"Alice"})
        assert manager.history == []

    asyncio.run(run())


def test_request_timeout_preserves_history():
    """Verify a request timeout preserves the history."""

    async def run():
        settings.llm.request_timeout_seconds = 0.01
        client = FakeClient()

        async def blocked(**kwargs):
            await asyncio.Event().wait()

        client.beta.chat.completions.parse = blocked
        manager = LLMContextManager(client)
        with pytest.raises(LLMResolutionError, match="failed"):
            await manager.generate_initial_state()
        assert manager.history == []

    asyncio.run(run())


def test_cancelling_compaction_keeps_original_memory_and_history():
    """Verify cancelling compaction keeps the original memory and history."""

    async def run():
        entered = asyncio.Event()
        client = FakeClient()

        async def blocked(**kwargs):
            entered.set()
            await asyncio.Event().wait()

        client.beta.chat.completions.parse = blocked
        manager = LLMContextManager(client)
        manager.memory = {"role": "user", "content": memory().model_dump_json()}
        manager.history = [
            {"role": "user", "content": "earlier action " * 180},
            {"role": "assistant", "content": "earlier state " * 100},
        ]
        previous_memory, previous_history = manager.memory, manager.history
        task = asyncio.create_task(
            manager._request(
                {"role": "user", "content": "next action " * 230}, RoundResolution, remember=True
            )
        )
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert manager.memory is previous_memory and manager.history is previous_history

    asyncio.run(run())
