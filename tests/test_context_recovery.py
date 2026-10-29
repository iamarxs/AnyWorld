"""Bounded optional compaction and concurrent tokenizer work."""

import asyncio

import httpx

from core.config import settings
from core.schemas import ContextSummary, RoundResolution, SummaryAudit
from logic.llm_manager import LLMContextManager
from support import FakeClient, memory
from openai import APIConnectionError
import pytest
from logic.llm.errors import LLMResolutionError


def test_failed_optional_checkpoint_does_not_block_a_fitting_round():
    async def run():
        settings.llm.history_round_limit = 2

        def respond(request):
            if issubclass(request["response_format"], ContextSummary):
                return memory()
            if request["response_format"] is SummaryAudit:
                return SummaryAudit(preserved=False, corrections=["Preserve the consumed vial."])
            return RoundResolution(
                global_narrative="Alice waits.", player_resolutions={"Alice": "Waits."}
            )

        manager = LLMContextManager(FakeClient(respond))
        manager.context_window_size = 32768
        manager.history = [
            {
                "role": "user" if index % 2 == 0 else "assistant",
                "content": "Established fact. " * 70,
            }
            for index in range(6)
        ]
        original = list(manager.history)
        await manager.generate_resolution({"Alice": "Wait"})
        assert manager.history[:6] == original
        assert manager.memory is None

    asyncio.run(run())


def test_concurrent_schema_counts_share_backend_work():
    async def run():
        settings.llm.provider = "compatible"
        manager = LLMContextManager(FakeClient())
        manager.budget._llama_cpp_detected = True
        paths = []

        async def respond(request):
            paths.append(request.url.path)
            await asyncio.sleep(0)
            if request.url.path == "/apply-template":
                return httpx.Response(200, json={"prompt": "Rendered"})
            return httpx.Response(200, json={"tokens": [1, 2, 3]})

        manager.budget._http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        messages = [{"role": "user", "content": "A stable fact."}]
        counts = await asyncio.gather(
            *(manager.budget.input_tokens(messages, None) for _ in range(5))
        )
        assert counts == [3] * 5
        assert paths == ["/apply-template", "/tokenize"]
        assert manager.budget._cache_bytes == len("A stable fact.")
        await manager.close()

    asyncio.run(run())


def test_exhausted_summary_transport_does_not_trigger_semantic_repair():
    async def run():
        settings.llm.max_retries = 0
        error = APIConnectionError(request=httpx.Request("POST", "http://offline.test/v1"))
        client = FakeClient(error)
        manager = LLMContextManager(client)
        manager.context_window_size = 8192
        manager.history = [
            {"role": "user" if index % 2 == 0 else "assistant", "content": "Fact. " * 200}
            for index in range(10)
        ]
        original = list(manager.history)
        with pytest.raises(LLMResolutionError):
            await manager.generate_resolution({"Alice": "Wait"})
        assert len(client.calls) == 1
        assert manager.history == original
        await manager.close()

    asyncio.run(run())
