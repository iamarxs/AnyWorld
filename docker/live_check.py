"""Opt-in real NVIDIA inference and public tunnel acceptance check.

Uses a read-only existing GGUF; isolated resources are removed on exit.
"""

import argparse
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
from tempfile import TemporaryDirectory
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
CHECK = r"""
import asyncio, hashlib, json, os, ssl, time
from pathlib import Path
from uuid import uuid4
import httpx, websockets
from core.config import settings

async def run():
    public = os.environ['TEST_PUBLIC_URL']
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(public)
        assert response.status_code == 200
        props = (await client.get('http://llama:8033/props')).json()
        print('Backend context:', props.get('default_generation_settings', {}).get('n_ctx'))
    private_tls = ssl.create_default_context(cafile='certs/cert.pem')
    for url, tls in [('https://localhost:4141', private_tls), (public, True)]:
        identity = str(uuid4())
        try:
            async with websockets.connect(url.replace('https:', 'wss:') + '/ws/' + identity,
                                          origin='https://evil.invalid', ssl=tls):
                raise AssertionError('Cross-origin WebSocket was accepted')
        except websockets.exceptions.InvalidStatus:
            pass
    identity = str(uuid4())
    async with websockets.connect(public.replace('https:', 'wss:') + '/ws/' + identity,
                                  origin=public, open_timeout=30) as ws:
        async def send(kind, data):
            await ws.send(json.dumps({'event_type':kind, 'data':data}))
        async def receive(kind):
            started = time.monotonic()
            while True:
                event = json.loads(await asyncio.wait_for(ws.recv(), 300))
                if event['type'] == 'error':
                    raise AssertionError(event['payload'])
                if event['type'] == kind:
                    print(kind, 'passed in', round(time.monotonic()-started, 2), 'seconds')
                    return event['payload']
        digest = hashlib.sha256((settings.server.host_password + identity).encode()).hexdigest()
        await send('auth', {'name':'Live Test Host', 'password_digest':digest})
        await receive('auth_ok')
        await send('scenario_init', {'scenario':
            'A quiet village library. Live Test Host is a visitor looking for a local map. '
            'A friendly librarian stands behind the desk. Keep descriptions brief.'})
        await receive('scenario_ready')
        await send('start_game', {})
        opening = await receive('state_update')
        assert opening['global_narrative'] and opening['player_resolutions'] == {}
        await receive('turn_directive')
        await send('action', {'action':'Ask the librarian where to find the village map.'})
        round_result = await receive('state_update')
        assert round_result['global_narrative']
        assert 'Live Test Host' in round_result['player_resolutions']
        await send('end_game', {})
        await receive('game_ended')
    archives = list(Path('.logged_games').glob('*.html'))
    assert archives and all(p.read_text().endswith('</html>\n') for p in archives)
    assert list(Path('.debug/llm').glob('*.log'))
    print('Public HTTPS/WSS, origin rejection, real title/opening/round and archive passed')
asyncio.run(run())
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", type=Path, required=True)
    args = parser.parse_args()
    model = args.model.resolve(strict=True)
    project = "anyworld-live-" + uuid4().hex[:8]
    image = project + ":test"
    environment = dict(os.environ)
    environment.update(
        HF_REPO="test/existing-model",
        HF_FILE="existing.gguf",
        HF_TOKEN="",
        MODEL_ALIAS="live-test",
        AD_SERVER__HOST_PASSWORD=secrets.token_urlsafe(24),
        AD_SERVER__PLAYER_PASSWORD=secrets.token_urlsafe(24),
        GAME_CONFIG=(ROOT / "docker/game.example.yaml").as_posix(),
        CHAT_TEMPLATE_FILE=(
            ROOT / "docker/templates/google-gemma-4-canonical-templ.jinja"
        ).as_posix(),
        TUNNEL_SUBNET="10.249.241.0/24",
        TUNNEL_IP="10.249.241.2",
        GAME_INGRESS_IP="10.249.241.3",
    )

    def run(command, **kwargs):
        return subprocess.run(command, env=environment, check=True, **kwargs)

    run(["docker", "build", "-t", image, str(ROOT)])
    try:
        with TemporaryDirectory(prefix=project) as directory:
            empty = Path(directory) / "empty.env"
            empty.touch()
            source = ["docker", "compose", "--env-file", str(empty), "-p", project]
            for file in [
                "compose.yaml",
                "compose.llama.yaml",
                "compose.debug.yaml",
            ]:
                source += ["-f", str(ROOT / file)]
            source += ["--profile", "tunnel"]
            config = json.loads(
                run(source + ["config", "--format", "json"], capture_output=True, text=True).stdout
            )
            game, llama = config["services"]["game"], config["services"]["llama"]
            # Keep acceptance checks away from the host's real archives.
            for mount in game["volumes"]:
                if mount["target"] in ("/app/.logged_games", "/app/.debug"):
                    folder = Path(directory)
                    folder = folder / Path(mount["target"]).name
                    folder.mkdir(parents=True, exist_ok=True)
                    folder.chmod(0o777)  # Disposable fake/test data; supports the container UID.
                    mount["source"] = folder.as_posix()
            game.pop("ports")
            game.pop("build")
            game["image"], game["pull_policy"] = image, "never"
            llama["environment"].pop("LLAMA_ARG_HF_REPO")
            llama["environment"].pop("LLAMA_ARG_HF_FILE")
            llama["command"] += ["--model", "/local/model.gguf"]
            llama["volumes"] = [v for v in llama["volumes"] if v["type"] == "bind"]
            llama["volumes"].append(
                dict(
                    type="bind", source=model.as_posix(), target="/local/model.gguf", read_only=True
                )
            )
            del config["volumes"]["models"]
            for service in config["services"].values():
                service["restart"] = "no"
            target = Path(directory) / "compose.json"
            target.write_text(json.dumps(config), encoding="utf-8")
            compose = ["docker", "compose", "-p", project, "-f", str(target), "--profile", "tunnel"]
            try:
                run(compose + ["up", "-d", "--wait", "--wait-timeout", "1800"])
                for _ in range(60):
                    logs = run(
                        compose + ["logs", "--no-color", "tunnel"], capture_output=True, text=True
                    ).stdout
                    match = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", logs)
                    if match:
                        public = match.group()
                        break
                    time.sleep(2)
                else:
                    raise AssertionError("Tunnel URL not found")
                time.sleep(8)
                run(
                    compose
                    + ["exec", "-T", "-e", "TEST_PUBLIC_URL=" + public, "game", "python", "-"],
                    input=CHECK,
                    text=True,
                )
                run(compose + ["stop"])
                logs = run(
                    compose + ["logs", "--no-color", "game"], capture_output=True, text=True
                ).stdout
                assert "Application shutdown complete." in logs
                assert "Share this address with players:" in logs and public in logs
                assert "uvicorn.error:" not in logs and "uvicorn.server:" in logs
                print("Live acceptance passed; temporary public tunnel is stopped")
            except Exception:
                result = subprocess.run(
                    compose + ["logs", "--tail", "80", "llama", "tunnel"],
                    env=environment,
                    capture_output=True,
                    text=True,
                )
                print(result.stdout)
                raise
            finally:
                run(compose + ["down", "--volumes", "--remove-orphans"])
    finally:
        run(["docker", "image", "rm", image])


if __name__ == "__main__":
    main()
