"""Configuration and boundary-schema tests."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from core.config import ConfigLoadError, LLMConfig, Settings
from core.schemas import ClientPayload, DicePlan, RoundResolution
from logic.llm_manager import participant_schema


@pytest.mark.parametrize("effort", ["none", "low", "medium", "high"])
def test_reasoning_effort_loads_from_yaml(tmp_path, effort):
    """Use one validated effort vocabulary for both providers."""
    config = tmp_path / "config.yaml"
    config.write_text(
        f'llm:\n  system_prompt: "Direct the game."\n  reasoning_effort: "{effort}"\n',
        encoding="utf-8",
    )
    assert Settings.load(config).llm.reasoning_effort == effort


@pytest.mark.parametrize("effort", [None, False, True, 1, "", "minimal", "xhigh", "HIGH"])
def test_reasoning_effort_rejects_unsupported_values(effort):
    """Reject legacy booleans and unsupported effort levels rather than silently defaulting."""
    with pytest.raises(ValidationError, match="reasoning_effort"):
        LLMConfig(system_prompt="Direct the game.", reasoning_effort=effort)


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


def test_configuration_discovery_preserves_local_and_explicit_files(tmp_path, monkeypatch):
    monkeypatch.delenv("AD_CONFIG_PATH", raising=False)
    assert Settings.load().server.host_password is None
    local = tmp_path / "config.yaml"
    local.write_text('llm:\n  system_prompt: "Local game."\n', encoding="utf-8")
    assert Settings.load().llm.system_prompt == "Local game."
    explicit = tmp_path / "operator.yaml"
    explicit.write_text('llm:\n  system_prompt: "Explicit game."\n', encoding="utf-8")
    monkeypatch.setenv("AD_CONFIG_PATH", str(explicit))
    assert Settings.load().llm.system_prompt == "Explicit game."
    assert local.read_text(encoding="utf-8").endswith('"Local game."\n')
    monkeypatch.setenv("AD_CONFIG_PATH", str(tmp_path / "missing.yaml"))
    with pytest.raises(ConfigLoadError, match="not found"):
        Settings.load()


def test_ad_environment_overrides_yaml_values(tmp_path: Path, monkeypatch) -> None:
    """Apply nested AD_ values without discarding other YAML settings."""
    config = tmp_path / "config.yaml"
    config.write_text(
        """
server:
    port: 9000
    host_password: "host"
    player_password: "player"
llm:
    model_name: "yaml-model"
    system_prompt: "Direct the game."
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("AD_SERVER__PORT", "4567")
    monkeypatch.setenv("AD_SERVER__HOST_PASSWORD", "environment-host")
    monkeypatch.setenv("AD_SERVER__PLAYER_PASSWORD", "environment-player")
    monkeypatch.setenv("AD_LLM__MODEL_NAME", "environment-model")

    loaded = Settings.load(config)

    assert loaded.server.port == 4567
    assert loaded.server.host_password == "environment-host"
    assert loaded.server.player_password == "environment-player"
    assert loaded.llm.model_name == "environment-model"
    assert loaded.llm.system_prompt == "Direct the game."


def test_shared_api_key_overrides_legacy_openai_key(tmp_path: Path, monkeypatch) -> None:
    """Prefer the shared API-key variable while retaining the legacy alias."""
    config = tmp_path / "config.yaml"
    config.write_text(
        """
llm:
    provider: "openai"
    model_name: "gpt-5.6-luna"
    api_key: "yaml-secret"
    system_prompt: "Direct the game."
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "stale-secret")
    monkeypatch.setenv("AD_LLM__API_KEY", "nested-secret")
    monkeypatch.setenv("AD_OPENAI_API_KEY", "test-secret")

    loaded = Settings.load(config)

    assert loaded.llm.api_key == "nested-secret"
    assert loaded.llm.temperature is None and loaded.llm.top_k is None


def test_openai_key_accepts_legacy_environment_alias(tmp_path: Path, monkeypatch) -> None:
    """Keep existing direct-OpenAI deployments working with their old key name."""
    config = tmp_path / "config.yaml"
    config.write_text(
        """
llm:
    provider: "openai"
    api_key: "yaml-secret"
    system_prompt: "Direct the game."
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("AD_OPENAI_API_KEY", "legacy-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "stale-secret")
    monkeypatch.delenv("AD_LLM__API_KEY", raising=False)

    assert Settings.load(config).llm.api_key == "legacy-secret"


def test_openai_key_is_required_without_either_supported_environment_name(
    tmp_path: Path, monkeypatch
) -> None:
    config = tmp_path / "config.yaml"
    config.write_text('llm:\n  provider: "openai"\n  system_prompt: "Direct the game."\n')
    monkeypatch.delenv("AD_OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("AD_LLM__API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "stale-secret")

    with pytest.raises(ConfigLoadError, match="AD_LLM__API_KEY"):
        Settings.load(config)


@pytest.mark.parametrize(
    "name,value",
    [
        ("temperature", -0.1),
        ("top_p", 1.1),
        ("top_k", -1),
        ("min_p", 1.1),
        ("presence_penalty", 2.1),
        ("repeat_penalty", 0),
    ],
)
def test_sampling_parameters_validate_ranges(name, value):
    with pytest.raises(ValidationError, match=name):
        LLMConfig(system_prompt="Direct the game.", **{name: value})


@pytest.mark.parametrize("name", ["top_k", "min_p", "repeat_penalty"])
def test_compatible_only_sampling_parameters_rejected_for_openai(name):
    with pytest.raises(ValidationError, match="require provider 'compatible'"):
        LLMConfig(
            system_prompt="Direct the game.",
            provider="openai",
            **{name: 1},
        )


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
        global_narrative="Time passes.",
        player_resolutions={"Alice": "Alice waits."},
    )

    assert payload.event_type == "action"
    assert resolution.player_resolutions["Alice"] == "Alice waits."
    with pytest.raises(ValidationError):
        ClientPayload.model_validate({"event_type": "action", "data": {}, "unexpected": True})
