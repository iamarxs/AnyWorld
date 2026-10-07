"""Resolve Compose offline and exercise the HTTPS proxy contract in-process."""

import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
from uuid import uuid4

import pytest
import yaml
from fastapi.testclient import TestClient
from starlette.websockets import WebSocket, WebSocketDisconnect

from api.admission import source_address
from api.server import create_app
from api.tls_bootstrap import ensure_cert
from core.config import Settings, settings
from support import FakeResolver
from logic.llm.tokenization import TokenBudget
from test_priority_one_transport import authenticate, receive_until

ROOT = Path(__file__).resolve().parents[1]


def compose_config(tmp_path, *, debug=False, tunnel=False, missing=None, openai=False):
    if not shutil.which("docker"):
        pytest.skip("Docker Compose CLI is needed; no daemon or downloads are used")
    values = {
        "HF_REPO": "" if openai else "test/model",
        "HF_FILE": "" if openai else "test.gguf",
        "HF_TOKEN": "",
        "OPENAI_MODEL": "test-openai-model",
        "AD_OPENAI_API_KEY": "test-only-not-a-real-key",
        "MODEL_ALIAS": "test-model",
        "AD_SERVER__HOST_PASSWORD": "test-host",
        "AD_SERVER__PLAYER_PASSWORD": "test-player",
        "GAME_CONFIG": (ROOT / "docker/game.example.yaml").as_posix(),
        "CHAT_TEMPLATE_FILE": "./docker/templates/google-gemma-4-canonical-templ.jinja",
        "LLAMA_IMAGE": "ghcr.io/ggml-org/llama.cpp:server-cuda",
        "CLOUDFLARED_IMAGE": "cloudflare/cloudflared:latest",
        "GAME_BIND_ADDRESS": "0.0.0.0",
        "GAME_PORT": "4141",
        "TLS_ADDRESSES": '["game","localhost","127.0.0.1"]',
        "MODEL_STARTUP_TIMEOUT": "3600s",
        "TUNNEL_SUBNET": "172.30.41.0/24",
        "TUNNEL_IP": "172.30.41.2",
        "GAME_INGRESS_IP": "172.30.41.3",
        "COMPOSE_PATH_SEPARATOR": "|",
        "COMPOSE_PROFILES": "",
    }
    if missing:
        values[missing] = ""
    files = ["compose.yaml", "compose.openai.yaml" if openai else "compose.llama.yaml"]
    if debug:
        files.append("compose.debug.yaml")
    values["COMPOSE_FILE"] = "|".join((ROOT / file).as_posix() for file in files)
    env_file = tmp_path / "deployment.env"
    env_file.write_text("\n".join(f"{key}={value}" for key, value in values.items()), "utf-8")
    command = ["docker", "compose", "--env-file", str(env_file)]
    if tunnel:
        command += ["--profile", "tunnel"]
    result = subprocess.run(
        command + ["config", "--format", "json"],
        env={key: value for key, value in os.environ.items() if key not in values},
        capture_output=True,
        text=True,
        timeout=30,
    )
    if missing:
        assert result.returncode != 0
        return
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    for service in config["services"].values():
        assert service["logging"] == {
            "driver": "json-file",
            "options": {"max-size": "10m", "max-file": "3"},
        }
    return config


@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("tunnel", [False, True])
def test_compose_variants_preserve_deployment_boundaries(tmp_path, debug, tunnel):
    config = compose_config(tmp_path, debug=debug, tunnel=tunnel)
    services = config["services"]
    game, llama = services["game"], services["llama"]
    assert llama["image"] == "ghcr.io/ggml-org/llama.cpp:server-cuda"
    assert "ports" not in llama
    assert game["depends_on"]["llama"]["condition"] == "service_healthy"
    assert game["environment"]["AD_LLM__ENDPOINT"] == "http://llama:8033/v1"
    assert game["environment"]["AD_LLM__MODEL_NAME"] == llama["environment"]["LLAMA_ARG_ALIAS"]
    assert game["environment"]["AD_LLM__DEBUG_RAW_RESPONSES"] == str(debug).lower()
    mounts = {mount["target"]: mount for mount in game["volumes"]}
    assert ("/app/.debug" in mounts) is debug
    assert "debug" not in config["volumes"]
    if debug:
        assert mounts["/app/.debug"]["type"] == "bind"
        assert Path(mounts["/app/.debug"]["source"]).parts[-2:] == ("data", "debug")
    assert mounts["/app/.logged_games"]["type"] == "bind"
    assert Path(mounts["/app/.logged_games"]["source"]).parts[-2:] == ("data", "logged_games")
    assert mounts["/app/certs"]["type"] == "volume"
    assert mounts["/config/game.yaml"]["read_only"]
    assert not mounts["/config/game.yaml"]["bind"]["create_host_path"]
    assert game["ports"][0]["host_ip"] == "0.0.0.0"
    assert game["ports"][0]["published"] == "4141"
    assert llama["healthcheck"]["start_period"] == "1h0m0s"
    assert llama["healthcheck"]["test"][-1] == "http://localhost:8033/health"
    assert "--reload" not in game["command"]
    assert llama["gpus"][0]["count"] == -1
    assert llama["ulimits"]["memlock"] == {"soft": -1, "hard": -1}
    assert llama["cap_add"] == ["IPC_LOCK"]
    template = next(mount for mount in llama["volumes"] if mount["type"] == "bind")
    assert template["read_only"] and template["target"] == "/templates/chat.jinja"
    assert Path(template["source"]).is_file()
    flags = dict(zip(llama["command"][::2], llama["command"][1::2]))
    # Flags after --context-shift are unpaired, so inspect those independently.
    assert flags["--fit-ctx"] == "131072" and flags["--reasoning-budget"] == "-1"
    assert flags["--load-mode"] == "mlock" and flags["--temp"] == "1.0"
    assert "--chat-template-file" in llama["command"]
    if tunnel:
        proxy = services["tunnel"]
        assert proxy["depends_on"]["game"]["condition"] == "service_healthy"
        assert proxy["user"] == "65532:65532"
        assert "ports" not in proxy
        assert proxy["command"][proxy["command"].index("--metrics") + 1] == "0.0.0.0:20241"
        assert game["environment"]["ANYWORLD_TUNNEL_METRICS"] == "http://tunnel:20241"
        assert "--no-tls-verify" not in proxy["command"]
        assert "--http-host-header" not in proxy["command"]
        assert "https://game:4141" in proxy["command"]
        assert proxy["volumes"][0]["read_only"]
        proxy_ip = proxy["networks"]["ingress"]["ipv4_address"]
        assert json.loads(game["environment"]["AD_SERVER__TRUSTED_PROXIES"]) == [proxy_ip + "/32"]


@pytest.mark.parametrize("missing", ["HF_REPO", "HF_FILE", "AD_SERVER__HOST_PASSWORD"])
def test_compose_requires_explicit_model_and_credentials(tmp_path, missing):
    compose_config(tmp_path, missing=missing)


@pytest.mark.parametrize("debug", [False, True])
@pytest.mark.parametrize("tunnel", [False, True])
def test_openai_selection_has_no_local_backend_or_model_requirements(tmp_path, debug, tunnel):
    config = compose_config(tmp_path, openai=True, debug=debug, tunnel=tunnel)
    assert "llama" not in config["services"] and "models" not in config["volumes"]
    game = config["services"]["game"]
    assert "depends_on" not in game
    assert game["environment"]["AD_LLM__PROVIDER"] == "openai"
    assert game["environment"]["AD_LLM__MODEL_NAME"] == "test-openai-model"
    assert game["environment"]["AD_LLM__DEBUG_RAW_RESPONSES"] == str(debug).lower()


@pytest.mark.parametrize("missing", ["OPENAI_MODEL", "AD_OPENAI_API_KEY"])
def test_openai_selection_requires_its_own_configuration(tmp_path, missing):
    compose_config(tmp_path, openai=True, missing=missing)


def test_game_example_loads_container_environment_and_preserves_prompt(monkeypatch):
    game = yaml.safe_load((ROOT / "docker/game.example.yaml").read_text("utf-8"))
    original = yaml.safe_load((ROOT / "config.example.yaml").read_text("utf-8"))
    assert game["llm"]["system_prompt"] == original["llm"]["system_prompt"]
    monkeypatch.setenv("AD_SERVER__TLS_ADDRESSES", '["game","localhost","127.0.0.1"]')
    monkeypatch.setenv("AD_SERVER__TRUSTED_PROXIES", '["172.30.41.2/32"]')
    monkeypatch.setenv("AD_LLM__DEBUG_RAW_RESPONSES", "false")
    monkeypatch.setenv("AD_LLM__OPENAI_CONTEXT_WINDOW_SIZE", "245760")
    loaded = Settings.load(ROOT / "docker/game.example.yaml")
    assert loaded.server.tls_addresses[0] == "game"
    assert loaded.server.trusted_proxies == ["172.30.41.2/32"]
    assert not loaded.llm.debug_raw_responses and loaded.llm.reasoning_effort == "none"
    assert loaded.llm.openai_context_window_size == 245760
    assert loaded.llm.openai_tokenizer_encoding == "auto"
    assert loaded.llm.tokenizer_encoding is None


@pytest.mark.parametrize(
    "provider, openai_limit, expected",
    [("compatible", 245760, 16384), ("openai", 245760, 245760), ("openai", None, 16384)],
)
def test_context_fallback_selects_provider_without_breaking_legacy_configs(
    provider, openai_limit, expected
):
    settings.llm.provider = provider
    settings.llm.context_window_size = 16384
    settings.llm.openai_context_window_size = openai_limit
    assert TokenBudget().context_window_size == expected


@pytest.mark.parametrize("hostname", ["game:4141", "temporary.trycloudflare.com"])
def test_https_direct_and_tunnel_authentication_and_origin_rejection(hostname):
    settings.server.allow_missing_origin = False
    application = create_app(FakeResolver)
    with TestClient(application, base_url="https://game") as client:
        identity = str(uuid4())
        with client.websocket_connect(
            f"wss://{hostname}/ws/{identity}", headers={"origin": f"https://{hostname}"}
        ) as socket:
            authenticate(socket, identity, name=hostname)
            assert receive_until(socket, "auth_ok")["type"] == "auth_ok"
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(
                f"wss://{hostname}/ws/{uuid4()}", headers={"origin": "https://evil.test"}
            ):
                pass
    assert not application.state.manager.active_connections


def test_only_tunnel_peer_can_supply_forwarded_client_identity():
    settings.server.trusted_proxies = ["172.30.41.2/32"]
    for peer, expected in (("172.30.41.2", "203.0.113.7"), ("192.0.2.5", "192.0.2.5")):
        socket = WebSocket(
            {
                "type": "websocket",
                "client": (peer, 12345),
                "headers": [(b"x-forwarded-for", b"198.51.100.4, 203.0.113.7")],
            },
            receive=None,
            send=None,
        )
        assert source_address(socket) == expected


def test_generated_certificate_can_be_explicitly_trusted_without_public_ip_discovery(monkeypatch):
    monkeypatch.setattr(
        "api.tls_bootstrap.get_external_ip",
        lambda: pytest.fail("Container launch must use configured addresses"),
    )
    _, certificate, _ = ensure_cert(["game", "localhost", "127.0.0.1"])
    context = ssl.create_default_context(cafile=certificate)
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
