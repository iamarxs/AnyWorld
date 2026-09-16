"""CLI debug overrides survive reload without starting a server or generating certificates."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

import app
from core.config import settings


@pytest.mark.parametrize("flag", ["--debug", "--debug-raw-responses"])
@pytest.mark.parametrize("reload", [False, True])
def test_debug_cli_enables_logging_for_current_and_reload_process(monkeypatch, flag, reload):
    """The launch flag affects both settings and the environment inherited by workers."""
    monkeypatch.delenv("ANYWORLD_DEBUG_RAW_RESPONSES", raising=False)
    monkeypatch.setattr(sys, "argv", ["anyworld", flag] + (["--reload"] if reload else []))
    monkeypatch.setattr(app, "ensure_cert", lambda: ("127.0.0.1", "cert", "key"))
    launches = []
    monkeypatch.setattr(app.uvicorn, "run", lambda *args, **kwargs: launches.append(kwargs))
    app.main()
    assert settings.llm.debug_raw_responses
    assert launches[0]["reload"] is reload
    assert os.environ["ANYWORLD_DEBUG_RAW_RESPONSES"] == "1"
    # Import configuration in a fresh interpreter, as Uvicorn's spawned reload child does.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from core.config import settings; print(settings.llm.debug_raw_responses)",
        ],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "True"


@pytest.mark.parametrize("configured", [False, True])
def test_no_cli_debug_flag_preserves_yaml_setting(monkeypatch, configured):
    """An ordinary launch neither enables debug nor overrides an explicit YAML opt-in."""
    monkeypatch.delenv("ANYWORLD_DEBUG_RAW_RESPONSES", raising=False)
    settings.llm.debug_raw_responses = configured
    monkeypatch.setattr(sys, "argv", ["anyworld"])
    monkeypatch.setattr(app, "ensure_cert", lambda: ("127.0.0.1", "cert", "key"))
    monkeypatch.setattr(app.uvicorn, "run", lambda *args, **kwargs: None)
    app.main()
    assert settings.llm.debug_raw_responses is configured
    assert "ANYWORLD_DEBUG_RAW_RESPONSES" not in os.environ
