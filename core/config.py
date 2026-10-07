"""Validated application configuration loaded from YAML."""

from pathlib import Path
import os
from typing import Any, Literal
from ipaddress import ip_network

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, EnvSettingsSource, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"
PACKAGED_CONFIG_PATH = Path(__file__).with_name("defaults.yaml")

# Load local secrets before settings are created, without overwriting variables supplied
# by the process environment. ``AD_OPENAI_API_KEY`` is consumed below only for the
# direct OpenAI provider.
load_dotenv(PROJECT_ROOT / ".env", override=False)


class ConfigLoadError(ValueError):
    """Raised when a configuration file cannot be parsed."""


class LLMConfig(BaseModel):
    """Inference configuration shared by direct and compatible OpenAI APIs."""

    model_config = ConfigDict(strict=True, extra="forbid")

    provider: Literal["compatible", "openai"] = "compatible"
    endpoint: str = Field(default="http://localhost:8033/v1", min_length=1)
    api_key: str = Field(default="sk-no-key-required", min_length=1)
    context_window_size: int = Field(default=8_192, ge=2_048)
    openai_context_window_size: int | None = Field(default=None, ge=2_048)
    tokenizer_encoding: str | None = Field(default="cl100k_base")
    openai_tokenizer_encoding: str | None = Field(default=None)
    model_name: str = Field(default="local", min_length=1)
    system_prompt: str = Field(min_length=1)
    initial_output_tokens: int = Field(default=4_096, ge=64)
    round_output_tokens: int = Field(default=4_096, ge=64)
    dice_output_tokens: int = Field(default=768, ge=64)
    summary_output_tokens: int = Field(default=3_072, ge=64)
    token_safety_margin: int = Field(default=256, ge=64)
    request_timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    max_retries: int = Field(default=1, ge=0, le=3)
    reasoning_effort: Literal["none", "low", "medium", "high"] = "none"
    debug_raw_responses: bool = False
    compaction_target_fraction: float = Field(default=0.75, ge=0.5, le=1.0)
    history_round_limit: int | None = Field(default=None, ge=2, le=100)


class ServerConfig(BaseModel):
    """HTTP and game-lobby configuration."""

    model_config = ConfigDict(strict=True, extra="forbid")

    host: str = Field(default="0.0.0.0", min_length=1)
    port: int = Field(default=4141, ge=1, le=65_535)
    host_password: str | None = Field(default=None, min_length=1)
    player_password: str | None = Field(default=None, min_length=1)
    max_players: int = Field(default=6, ge=1, le=100)
    tls_addresses: list[str] = Field(default_factory=list)
    tls_certfile: str | None = None
    tls_keyfile: str | None = None
    max_pending_connections: int = Field(default=32, ge=1, le=1_000)
    auth_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    max_auth_attempts: int = Field(default=3, ge=1, le=10)
    max_message_bytes: int = Field(default=65_536, ge=1024, le=1_048_576)
    max_message_depth: int = Field(default=8, ge=2, le=32)
    failed_logins_per_source: int = Field(default=30, ge=1)
    failed_logins_global: int = Field(default=120, ge=1)
    allowed_origins: list[str] = Field(default_factory=list)
    allow_missing_origin: bool = False
    trusted_proxies: list[str] = Field(default_factory=list)
    player_messages_per_window: int = Field(default=30, ge=1)
    player_message_window_seconds: float = Field(default=10.0, gt=0)
    send_queue_messages: int = Field(default=128, ge=1)
    send_queue_bytes: int = Field(default=2_097_152, ge=65_536)

    @model_validator(mode="after")
    def passwords_must_differ(self) -> "ServerConfig":
        """Reject a configuration where host and player passwords are equal."""
        for network in self.trusted_proxies:
            ip_network(network, strict=False)
        if bool(self.tls_certfile) != bool(self.tls_keyfile):
            raise ValueError("tls_certfile and tls_keyfile must be configured together")
        if (
            self.host_password is not None
            and self.player_password is not None
            and self.host_password == self.player_password
        ):
            raise ValueError("host_password and player_password must differ")
        return self

    def validate_passwords(self) -> None:
        """Reject an unsafe launch until both passwords are explicitly configured."""
        if not self.host_password or not self.player_password:
            raise ValueError(
                "host_password and player_password must be set in config.yaml or "
                "AD_SERVER__HOST_PASSWORD/AD_SERVER__PLAYER_PASSWORD before launch"
            )
        if self.host_password == self.player_password:
            raise ValueError("host_password and player_password must differ")


class Settings(BaseSettings):
    """Root settings model and YAML loader with environment overrides."""

    model_config = SettingsConfigDict(
        strict=True,
        extra="forbid",
        env_prefix="AD_",
        env_nested_delimiter="__",
    )

    llm: LLMConfig = Field(default_factory=LLMConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)

    @classmethod
    def load(cls, path: str | Path | None = None) -> "Settings":
        """Load YAML, apply ``AD_`` overrides, and validate the merged settings.

        When the direct OpenAI provider is selected, ``AD_OPENAI_API_KEY`` is the only
        accepted API-key source. It is never written back to the configuration file or
        included in diagnostics.
        """
        if path is not None:
            config_path = Path(path)
        elif os.environ.get("AD_CONFIG_PATH"):
            config_path = Path(os.environ["AD_CONFIG_PATH"])
        elif Path("config.yaml").is_file():
            config_path = Path("config.yaml")
        else:
            config_path = PACKAGED_CONFIG_PATH
        try:
            with config_path.open("r", encoding="utf-8") as config_file:
                raw_data: Any = yaml.safe_load(config_file)
        except FileNotFoundError as exc:
            raise ConfigLoadError(f"Configuration file not found: {config_path}") from exc
        except yaml.YAMLError as exc:
            raise ConfigLoadError(f"Malformed YAML in {config_path}: {exc}") from exc
        except OSError as exc:
            raise ConfigLoadError(f"Cannot read configuration {config_path}: {exc}") from exc

        if raw_data is None:
            raw_data = {}
        if not isinstance(raw_data, dict):
            raise ConfigLoadError(f"Configuration root must be a mapping: {config_path}")

        environment_data = EnvSettingsSource(cls)()
        merged_data = cls._merge_mappings(raw_data, environment_data)
        llm_data = merged_data.get("llm")
        openai_api_key = os.environ.get("AD_OPENAI_API_KEY")
        if isinstance(llm_data, dict) and llm_data.get("provider") == "openai":
            if not openai_api_key:
                raise ConfigLoadError("AD_OPENAI_API_KEY must be set when provider is 'openai'")
            # Keep the secret in memory only; never include it in config diagnostics/logs.
            llm_data["api_key"] = openai_api_key
        return cls.model_validate(merged_data)

    @staticmethod
    def _merge_mappings(values: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
        """Recursively apply environment values over the YAML mapping."""
        merged = dict(values)
        for key, value in overrides.items():
            current = merged.get(key)
            if isinstance(current, dict) and isinstance(value, dict):
                merged[key] = Settings._merge_mappings(current, value)
            else:
                merged[key] = value
        return merged


settings = Settings.load()
if os.environ.get("ANYWORLD_DEBUG_RAW_RESPONSES") == "1":
    settings.llm.debug_raw_responses = True
