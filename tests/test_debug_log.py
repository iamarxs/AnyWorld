"""Raw model diagnostics use mocked HTTP and never contact a backend."""

import asyncio
import json

import httpx
import pytest

from core.config import settings
from core.schemas import RoundResolution
from logic import llm_manager
from logic.debug_log import RawResponseLogger
from logic.llm_manager import LLMContextManager, LLMResolutionError


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("response_kind", ["valid", "invalid_json", "http_error"])
def test_raw_logging_precedes_sdk_parsing_and_does_not_change_requests(
    tmp_path, monkeypatch, enabled, response_kind, caplog
):
    """Capture exact response text, including parse failures, only when opted in."""
    monkeypatch.chdir(tmp_path)
    settings.llm.provider = "compatible"
    settings.llm.endpoint = "http://model.invalid/v1"
    settings.llm.debug_raw_responses = enabled
    narrative = "Arxs steps cautiouslyจาก beside PRIVATE_DIAGNOSTIC."
    content = json.dumps(
        {
            "round_title": None,
            "global_narrative": narrative,
            "player_resolutions": {"Arxs": "Arxs waits."},
        },
        ensure_ascii=False,
    )
    if response_kind == "invalid_json":
        content = "PRIVATE_DIAGNOSTIC invalid model JSON"
    body = json.dumps(
        {
            "id": "probe",
            "object": "chat.completion",
            "created": 0,
            "model": "test",
            "choices": [
                {
                    "index": 0,
                    "finish_reason": "stop",
                    "message": {"role": "assistant", "content": content},
                }
            ],
        },
        ensure_ascii=False,
        indent=2,
    )
    if response_kind == "http_error":
        body = '{"error":{"message":"PRIVATE_DIAGNOSTIC","type":"server_error"}}'

    def handler(request):
        request_json = json.loads(request.content)
        assert "repeat_penalty" not in request_json
        assert "presence_penalty" not in request_json
        return httpx.Response(
            503 if response_kind == "http_error" else 200,
            text=body,
            headers={"content-type": "application/json"},
        )

    # Both normal SDK construction and diagnostic SDK construction use the mock.
    from openai import DefaultAsyncHttpxClient

    def client_factory(**kwargs):
        return DefaultAsyncHttpxClient(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(llm_manager, "DefaultAsyncHttpxClient", client_factory)

    async def run():
        manager = LLMContextManager()
        if not enabled:
            from openai import AsyncOpenAI

            manager.client = AsyncOpenAI(
                api_key="test",
                base_url=settings.llm.endpoint,
                max_retries=0,
                http_client=client_factory(),
            )
        try:
            if response_kind == "valid":
                result = await manager._parse_attempt([], RoundResolution, "round", 100, 0)
                assert result.global_narrative == narrative
            else:
                with pytest.raises(LLMResolutionError):
                    await manager._parse_attempt([], RoundResolution, "round", 100, 0)
        finally:
            await manager.close()

    asyncio.run(run())
    files = list((tmp_path / ".debug" / "llm").glob("*.json"))
    assert len(files) == int(enabled)
    if enabled:
        record = json.loads(files[0].read_text(encoding="utf-8"))
        assert record["body"] == body
        assert set(record) == {"timestamp", "status_code", "body"}
    else:
        assert not (tmp_path / ".debug").exists()
    assert "PRIVATE_DIAGNOSTIC" not in caplog.text


def test_debug_disk_failure_does_not_break_response(tmp_path):
    """Optional diagnostics must not pause the game when the disk is unwritable."""
    target = tmp_path / "not_a_directory"
    target.write_text("existing file", encoding="utf-8")
    logger = RawResponseLogger(target)

    async def run():
        response = httpx.Response(
            200,
            text="raw",
            request=httpx.Request("POST", "http://model.invalid/v1/chat/completions"),
        )
        await logger.capture(response)
        assert response.text == "raw"

    asyncio.run(run())
