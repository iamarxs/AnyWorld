"""Opt-in Docker checks with a fake backend and disposable storage, without published ports."""

import argparse
import json
import os
from pathlib import Path
import secrets
import subprocess
import time
from tempfile import TemporaryDirectory
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]

FAKE_BACKEND = """
from http.server import BaseHTTPRequestHandler, HTTPServer
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"status":"ok"}')
    def log_message(self, *args):
        pass
HTTPServer(('0.0.0.0', 8033), Handler).serve_forever()
"""

CHECK_GAME = """
import asyncio, hashlib, json, os, ssl
from pathlib import Path
from uuid import uuid4
import httpx
import websockets
from core.config import settings
from logic.debug_log import RawResponseLogger
from logic.transcript import GameTranscript

assert os.getuid() == 10001
assert not Path('/app/config.yaml').exists()
assert not Path('/app/.env').exists()
context = ssl.create_default_context(cafile='certs/cert.pem')
async def run():
    identity = str(uuid4())
    async with websockets.connect(
        'wss://localhost:4141/ws/' + identity,
        origin='https://localhost:4141', ssl=context,
    ) as socket:
        digest = hashlib.sha256(
            (os.environ['AD_SERVER__HOST_PASSWORD'] + identity).encode()
        ).hexdigest()
        await socket.send(json.dumps({'event_type':'auth', 'data': {
            'name':'Smoke Host', 'password_digest':digest}}))
        for _ in range(10):
            event = json.loads(await asyncio.wait_for(socket.recv(), 5))
            if event['type'] == 'auth_ok':
                break
        else:
            raise AssertionError('Authentication did not complete')
    try:
        async with websockets.connect(
            'wss://localhost:4141/ws/' + str(uuid4()),
            origin='https://evil.invalid', ssl=context,
        ):
            raise AssertionError('Cross-origin connection was accepted')
    except websockets.exceptions.InvalidStatus:
        pass
    archive = GameTranscript()
    await archive.start('Smoke', 'Offline opening')
    await archive.finalize()
    assert archive.path.read_text().endswith('</html>\\n')
    if settings.llm.debug_raw_responses:
        logger = RawResponseLogger()
        await logger.capture_request(httpx.Request(
            'POST', 'http://fake/v1/chat/completions', json={'model':'smoke'}
        ))
        assert list(Path('.debug/llm').glob('*.log'))
    else:
        assert not list(Path('.debug').rglob('*.log'))
asyncio.run(run())
print('HTTPS auth, origin rejection, non-root archive and debug checks passed')
"""


def main():
    """Build once and smoke-test both providers with normal/debug configurations."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tunnel", action="store_true", help="Also check a live Quick Tunnel banner."
    )
    args = parser.parse_args()
    project = "anyworld-smoke-" + uuid4().hex[:10]
    image = project + ":test"
    environment = dict(os.environ)
    # Explicit test values prevent reading the user's deployment secrets/configuration.
    environment.update(
        HF_REPO="smoke/not-downloaded",
        HF_FILE="not-downloaded.gguf",
        HF_TOKEN="",
        OPENAI_MODEL="smoke-not-called",
        AD_OPENAI_API_KEY="smoke-not-a-real-key",
        MODEL_ALIAS="smoke",
        AD_SERVER__HOST_PASSWORD=secrets.token_urlsafe(24),
        AD_SERVER__PLAYER_PASSWORD=secrets.token_urlsafe(24),
        GAME_CONFIG=(ROOT / "docker/game.example.yaml").as_posix(),
        CHAT_TEMPLATE_FILE=(
            ROOT / "docker/templates/google-gemma-4-canonical-templ.jinja"
        ).as_posix(),
        GAME_BIND_ADDRESS="127.0.0.1",
        GAME_PORT="4141",
        TLS_ADDRESSES='["game","localhost","127.0.0.1"]',
        TUNNEL_SUBNET="10.249.{}.0/24".format(int(uuid4().hex[:2], 16)),
        MODEL_STARTUP_TIMEOUT="30s",
        LLAMA_IMAGE=image,
    )
    network = environment["TUNNEL_SUBNET"].rsplit(".", 1)[0]
    environment.update(TUNNEL_IP=network + ".2", GAME_INGRESS_IP=network + ".3")

    def run(command, **kwargs):
        return subprocess.run(command, env=environment, check=True, **kwargs)

    run(["docker", "info", "--format", "{{.ServerVersion}}"])
    run(["docker", "build", "-t", image, str(ROOT)])
    try:
        with TemporaryDirectory(prefix=project) as directory:
            empty_env = Path(directory) / "empty.env"
            empty_env.touch()
            for backend, debug in (
                ("llama", False),
                ("llama", True),
                ("openai", False),
                ("openai", True),
            ):
                variant = project + "-" + backend + ("-debug" if debug else "-normal")
                source = [
                    "docker",
                    "compose",
                    "-p",
                    variant,
                    "--env-file",
                    str(empty_env),
                    "-f",
                    str(ROOT / "compose.yaml"),
                    "-f",
                    str(ROOT / f"compose.{backend}.yaml"),
                ]
                use_tunnel = args.tunnel and backend == "llama" and not debug
                if use_tunnel:
                    source += ["--profile", "tunnel"]
                if debug:
                    source += ["-f", str(ROOT / "compose.debug.yaml")]
                resolved = run(
                    source + ["config", "--format", "json"], capture_output=True, text=True
                )
                config = json.loads(resolved.stdout)
                if not use_tunnel:
                    config["services"].pop("tunnel", None)
                game = config["services"]["game"]
                # Keep acceptance checks away from the host's real archives.
                for mount in game["volumes"]:
                    if mount["target"] in ("/app/.logged_games", "/app/.debug"):
                        folder = Path(directory) / variant
                        folder = folder / Path(mount["target"]).name
                        folder.mkdir(parents=True, exist_ok=True)
                        # Disposable fake/test data; supports the container UID.
                        folder.chmod(0o777)
                        mount["source"] = folder.as_posix()
                game.pop("ports")
                game.pop("build")
                game["image"], game["pull_policy"] = image, "never"
                if backend == "llama":
                    llama = config["services"]["llama"]
                    llama["image"], llama["pull_policy"] = image, "never"
                    llama["entrypoint"] = ["python", "-c"]
                    llama["command"] = [FAKE_BACKEND]
                    llama["environment"] = {}
                    for option in ("gpus", "cap_add", "ulimits"):
                        llama.pop(option, None)
                    llama.pop("volumes")
                    del config["volumes"]["models"]
                    llama["healthcheck"]["test"] = [
                        "CMD",
                        "python",
                        "-c",
                        "import urllib.request; "
                        "urllib.request.urlopen('http://localhost:8033/health')",
                    ]
                    llama["healthcheck"]["interval"] = "1s"
                else:
                    assert "llama" not in config["services"] and "models" not in config["volumes"]
                game["healthcheck"]["interval"] = "1s"
                target = Path(directory) / (variant + ".json")
                target.write_text(json.dumps(config), encoding="utf-8")
                compose = ["docker", "compose", "-p", variant, "-f", str(target)]
                if use_tunnel:
                    compose += ["--profile", "tunnel"]
                try:
                    run(compose + ["up", "-d", "--wait", "--wait-timeout", "90"])
                    run(
                        compose + ["exec", "-T", "game", "python", "-"], input=CHECK_GAME, text=True
                    )
                    if use_tunnel:
                        for _ in range(65):
                            logs = run(
                                compose + ["logs", "--no-color", "game"],
                                capture_output=True,
                                text=True,
                            ).stdout
                            if "Share this address with players:" in logs:
                                assert "https://" in logs and ".trycloudflare.com" in logs
                                assert "uvicorn.error:" not in logs
                                assert "uvicorn.server:" in logs
                                print("Live Quick Tunnel URL announcement and logger alias passed")
                                break
                            time.sleep(2)
                        else:
                            raise AssertionError("Connected tunnel banner was not logged")
                    # The connector identity can read its trust anchor, never the private key.
                    run(
                        compose
                        + [
                            "exec",
                            "-T",
                            "--user",
                            "65532:65532",
                            "game",
                            "python",
                            "-c",
                            "import os; assert os.access('certs/cert.pem', os.R_OK); "
                            "assert not os.access('certs/key.pem', os.R_OK)",
                        ]
                    )
                    run(compose + ["stop"])
                    container = run(
                        compose + ["ps", "-a", "-q", "game"], capture_output=True, text=True
                    ).stdout.strip()
                    status = run(
                        ["docker", "inspect", "--format", "{{.State.ExitCode}}", container],
                        capture_output=True,
                        text=True,
                    ).stdout.strip()
                    logs = run(["docker", "logs", container], capture_output=True, text=True)
                    # Uvicorn may re-raise SIGTERM after its completed lifespan cleanup.
                    assert status in {"0", "143"}, "Game was forcibly terminated"
                    assert "Application shutdown complete." in logs.stdout + logs.stderr
                    run(
                        compose + ["up", "-d", "--force-recreate", "--wait", "--wait-timeout", "90"]
                    )
                    run(
                        compose
                        + [
                            "exec",
                            "-T",
                            "game",
                            "python",
                            "-c",
                            "from pathlib import Path; "
                            "assert list(Path('.logged_games').glob('*.html')); "
                            f"assert bool(list(Path('.debug').rglob('*.log'))) == {debug!r}",
                        ]
                    )
                    print(f"{backend}: {'debug' if debug else 'normal'} recreation checks passed")
                finally:
                    run(compose + ["down", "--volumes", "--remove-orphans"])
    finally:
        run(["docker", "image", "rm", image])


if __name__ == "__main__":
    main()
