"""Tests use isolated settings, never the user's passwords or live backend."""

import pytest

from core.config import LLMConfig, ServerConfig, settings


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch):
    monkeypatch.setattr(
        settings,
        "server",
        ServerConfig(host_password="test-host-password", player_password="test-player-password"),
    )
    monkeypatch.setattr(
        settings,
        "llm",
        LLMConfig(
            provider="openai",
            api_key="test-only",
            model_name="test-only",
            system_prompt="Keep coherent public outcomes; never expose private DM guidance.",
            tokenizer_encoding=None,
        ),
    )
