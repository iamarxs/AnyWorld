"""Validated application configuration loaded from YAML."""

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.yaml"


class ConfigLoadError(ValueError):
    """Raised when a configuration file cannot be parsed."""


class LLMConfig(BaseModel):
    """OpenAI-compatible inference configuration."""

    model_config = ConfigDict(strict=True, extra="forbid")

    provider: Literal["compatible", "openai"] = "compatible"
    endpoint: str = Field(default="http://localhost:8033/v1", min_length=1)
    api_key: str = Field(default="sk-no-key-required", min_length=1)
    context_window_size: int = Field(default=8_192, ge=2_048)
    tokenizer_encoding: str | None = Field(default="cl100k_base")
    model_name: str = Field(default="local", min_length=1)
    system_prompt: str = Field(min_length=1)
    initial_output_tokens: int = Field(default=1_024, ge=64)
    round_output_tokens: int = Field(default=2_048, ge=64)
    dice_output_tokens: int = Field(default=512, ge=64)
    summary_output_tokens: int = Field(default=1_024, ge=64)
    token_safety_margin: int = Field(default=256, ge=64)
    request_timeout_seconds: float = Field(default=120.0, gt=0, le=600)
    max_retries: int = Field(default=1, ge=0, le=3)


class ServerConfig(BaseModel):
    """HTTP and game-lobby configuration."""

    model_config = ConfigDict(strict=True, extra="forbid")

    host: str = Field(default="0.0.0.0", min_length=1)
    port: int = Field(default=4141, ge=1, le=65_535)
    host_password: str | None = Field(default=None, min_length=1)
    player_password: str | None = Field(default=None, min_length=1)
    max_players: int = Field(default=6, ge=1, le=100)
    max_pending_connections: int = Field(default=32, ge=1, le=1_000)
    auth_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    max_auth_attempts: int = Field(default=3, ge=1, le=10)

    @model_validator(mode="after")
    def passwords_must_differ(self) -> "ServerConfig":
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
                "host_password and player_password must be set in config.yaml before launch"
            )
        if self.host_password == self.player_password:
            raise ValueError("host_password and player_password must differ")


class Settings(BaseSettings):
    """Root settings model and YAML loader."""

    model_config = SettingsConfigDict(
        strict=True,
        extra="forbid",
        env_prefix="AD_",
        env_nested_delimiter="__",
    )

    llm: LLMConfig = Field(default_factory=LLMConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)

    @classmethod
    def load(cls, path: str | Path = DEFAULT_CONFIG_PATH) -> "Settings":
        config_path = Path(path)
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
        return cls.model_validate(raw_data)


settings = Settings.load()
