"""Configuration and boundary-schema tests."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from core.config import ConfigLoadError, Settings
from core.schemas import ClientPayload, DicePlan, RoundResolution
from logic.llm_manager import LLMContextManager, LLMResolutionError, participant_schema


def test_settings_loads_typed_yaml(tmp_path: Path) -> None:
    """Load a valid YAML config into typed settings."""
    config = tmp_path / "config.yaml"
    config.write_text(
        """
server:
    host: "127.0.0.1"
    port: 9000
    host_password: "host"
    player_password: "player"
    max_players: 8
llm:
    endpoint: "http://localhost:8080/v1"
    api_key: "key"
    context_window_size: 4096
    model_name: "model"
    system_prompt: "Direct the game."
""",
        encoding="utf-8",
    )

    loaded = Settings.load(config)

    assert loaded.server.port == 9000
    assert loaded.llm.context_window_size == 4096


def test_openai_api_key_comes_from_environment(tmp_path: Path, monkeypatch) -> None:
    """Use OPENAI_API_KEY for the direct provider without requiring it in YAML."""
    config = tmp_path / "config.yaml"
    config.write_text(
        """
llm:
    provider: "openai"
    model_name: "gpt-5.6-luna"
    system_prompt: "Direct the game."
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")

    loaded = Settings.load(config)

    assert loaded.llm.api_key == "test-secret"


def test_openai_schema_omits_unsupported_strict_keywords() -> None:
    """Keep provider-specific schema limits out of OpenAI's strict response schema."""
    schema = participant_schema(DicePlan, ("Alice",), provider="openai").model_json_schema()
    rendered = str(schema)

    assert "uniqueItems" not in rendered
    assert "maxItems" not in rendered


def test_settings_reports_malformed_yaml(tmp_path: Path) -> None:
    """Report malformed YAML as a ConfigLoadError."""
    config = tmp_path / "config.yaml"
    config.write_text("server: [broken", encoding="utf-8")

    with pytest.raises(ConfigLoadError, match="Malformed YAML"):
        Settings.load(config)


def test_settings_strictly_rejects_wrong_types(tmp_path: Path) -> None:
    """Reject wrong YAML types under strict validation."""
    config = tmp_path / "config.yaml"
    config.write_text('server:\n  port: "9000"\n', encoding="utf-8")

    with pytest.raises(ValidationError):
        Settings.load(config)


def test_websocket_and_resolution_schemas_are_strict() -> None:
    """Validate strict websocket and resolution schemas."""
    payload = ClientPayload(event_type="action", data={"action": "wait"})
    resolution = RoundResolution(
        round_title=None,
        global_narrative="Time passes.",
        player_resolutions={"Alice": "Alice waits."},
    )

    assert payload.event_type == "action"
    assert resolution.player_resolutions["Alice"] == "Alice waits."
    with pytest.raises(ValidationError):
        ClientPayload.model_validate({"event_type": "action", "data": {}, "unexpected": True})


def test_context_overflow_preserves_history_without_fifo_loss() -> None:
    """Preserve history when a bounded check overflows the budget."""
    manager = LLMContextManager()
    manager.context_window_size = 128_000
    manager.set_genesis("A short beginning")
    # Spaces prevent BPE from collapsing the fixture into a tiny repeated-token run.
    large_message = "x " * 40_000
    manager.history = [
        {"role": "user", "content": large_message},
        {"role": "assistant", "content": large_message},
        {"role": "user", "content": large_message},
        {"role": "assistant", "content": large_message},
    ]

    original = list(manager.history)
    with pytest.raises(LLMResolutionError, match="memory preserved"):
        manager._bounded_messages({"role": "user", "content": "Act"})
    assert manager.history == original
