"""Offline accounting regressions, not measured backend cache performance."""

import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from openai import APIConnectionError

from core.schemas import ContextSummary, DicePlan, RoundResolution
from logic.llm_manager import LLMContextManager, LLMResolutionError
from logic.engine import GameEngine
from test_engine import FakeSender
from test_priority_one_llm import FakeClient, memory


class MeteredClient(FakeClient):
    """Return controlled provider counters, including cache hits."""

    async def parse(self, **kwargs):
        """Attach complete usage to a normal fake response."""
        response = await super().parse(**kwargs)
        response.usage = SimpleNamespace(
            prompt_tokens=100,
            completion_tokens=20,
            total_tokens=120,
            prompt_tokens_details=SimpleNamespace(cached_tokens=60),
        )
        response.timings = {"prompt_n": 40, "cache_n": 60, "predicted_n": 20}
        return response


def test_compacting_round_counts_all_calls_and_host_retry(caplog):
    """Dice, summary and resolution count once, independently of retained context."""

    async def run():
        client = MeteredClient()
        manager = LLMContextManager(client)
        manager.set_genesis("A gate.", "PRIVATE_TRIGGER guards the key.")
        await manager.generate_initial_state()
        manager.begin_round_usage(1)
        await manager.plan_dice({"Alice": "Wait."})
        manager.history = [
            {"role": "user", "content": "Alice examines the gate. " * 100},
            {"role": "assistant", "content": memory().model_dump_json() + " detail" * 140},
        ]
        await manager._request(
            {"role": "user", "content": "Wait. " * 350}, RoundResolution, remember=True
        )
        assert sum(issubclass(c["response_format"], ContextSummary) for c in client.calls) == 1
        snapshot = manager.usage_snapshot()
        assert snapshot["round"]["total_tokens"] == 480
        assert snapshot["game"]["total_tokens"] == 600
        assert snapshot["round"]["cached_tokens"] == 240
        assert snapshot["round"]["processed_prompt_tokens"] == 160
        assert snapshot["round"]["reused_prompt_tokens"] == 240
        assert set(snapshot["round_by_kind"]) == {"dice", "summary", "summary_audit", "round"}
        assert snapshot["retained_context_tokens"] != 480
        manager.begin_round_usage(1)
        assert manager.usage_snapshot()["round"]["attempts"] == 4
        manager.begin_round_usage(2)
        assert manager.usage_snapshot()["round"]["attempts"] == 0
        assert "PRIVATE_TRIGGER" not in json.dumps(snapshot)
        assert "PRIVATE_TRIGGER" not in caplog.text
        manager.set_genesis("New game")
        assert manager.usage_snapshot()["game"]["attempts"] == 0

    caplog.set_level("INFO", logger="logic.llm_manager")
    asyncio.run(run())


def test_transient_retry_and_unknown_counters():
    """Failed attempts cannot turn unknown billing or cache usage into zero."""

    async def run():
        client = MeteredClient()
        original = client.parse
        calls = 0

        async def flaky(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise APIConnectionError(request=httpx.Request("POST", "http://test"))
            return await original(**kwargs)

        client.beta.chat.completions.parse = flaky
        manager = LLMContextManager(client)
        manager.begin_round_usage(1)
        await manager.generate_initial_state()
        usage = manager.usage_snapshot()["round"]
        assert (usage["attempts"], usage["errors"], usage["retries"]) == (2, 1, 1)
        assert usage["total_tokens"] is None
        assert usage["cached_tokens"] is None
        assert usage["known_tokens"]["total_tokens"] == 120
        assert usage["missing_counters"]["total_tokens"] == 1

    asyncio.run(run())


@pytest.mark.parametrize("truncated", [False, True])
def test_response_usage_survives_output_failure(truncated):
    """Unusable model output still incurs consumption."""

    async def run():
        manager = LLMContextManager(MeteredClient(finish_reason="length" if truncated else "stop"))
        if truncated:
            with pytest.raises(LLMResolutionError):
                await manager.generate_initial_state()
        else:
            await manager.generate_initial_state()
        usage = manager.usage_snapshot()["game"]
        assert usage["total_tokens"] == 120
        assert usage["errors"] == int(truncated)

    asyncio.run(run())


def test_cancelled_attempt_is_recorded():
    """Cancellation records unknown usage and preserves history."""

    async def run():
        entered = asyncio.Event()
        client = FakeClient()

        async def blocked(**kwargs):
            entered.set()
            await asyncio.Event().wait()

        client.beta.chat.completions.parse = blocked
        manager = LLMContextManager(client)
        task = asyncio.create_task(manager.generate_initial_state())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        usage = manager.usage_snapshot()
        assert usage["game"]["attempts"] == usage["game"]["errors"] == 1
        assert usage["last_request"]["error"] == "CancelledError"
        assert manager.history == []

    asyncio.run(run())


def test_engine_reports_semantic_failure_and_retains_cost_on_retry(tmp_path, monkeypatch):
    """A parsed but rejected plan is a failed round, even without an HTTP error."""

    async def run():
        monkeypatch.chdir(tmp_path)
        manager = LLMContextManager(MeteredClient(DicePlan(rolls={"Alice": True}, hidden_rolls=[])))
        sender = FakeSender()
        engine = GameEngine(sender, manager)
        engine.pending_resolution = {
            "actions": {"id": "Wait"},
            "participants": {"id": ("Borin", False, False, 0)},
            "dice": None,
            "previous_state": "A gate",
        }
        for expected in (1, 2):
            with pytest.raises(LLMResolutionError, match="participants"):
                await engine._resolve_round(0)
            await engine._publish_usage()
            payload = sender.events_of_type("token_usage")[-1].payload
            assert payload["round_failures"] == expected
            assert payload["round"]["attempts"] == 2 * expected
            assert payload["round"]["total_tokens"] == 240 * expected
            assert payload["round"]["errors"] == 0
            assert payload["last_round_error"] == "LLMResolutionError"
            assert payload["round_work_seconds"] > 0

    asyncio.run(run())
