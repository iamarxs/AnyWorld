# Run Anyworld with Docker

First [download the project](INSTALL.md#download-the-project). Run the commands
below in the folder containing `compose.yaml`. Install Docker Compose 2.30 or
newer and start Docker. On Windows, use [Docker Desktop](https://docs.docker.com/desktop/) with Linux containers and
its WSL2 backend. Local llama.cpp needs an NVIDIA GPU and a compatible driver;
Linux also needs [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html).
OpenAI requires no local GPU. Verify the engine with `docker info`. On Linux,
complete [folder permissions setup](#saved-files) before launching either backend.

## Quick Startup

This starts **local llama.cpp on an NVIDIA GPU**, the recommended model for
16 GB VRAM, and an account-free public tunnel.

1. From the project root, copy the deployment settings:

   ```powershell
   Copy-Item docker/deployment.env.example docker.env
   ```

   On Linux, use `cp docker/deployment.env.example docker.env` instead.
   If you already have `docker.env`, edit it instead of replacing it.

2. Set these values in `docker.env`, including distinct host and player passwords:

   ```dotenv
   COMPOSE_PROFILES=tunnel
   HF_REPO=EZForever/gemma-4-26B-A4B-it-qat-uncensored-heretic-UDmerge-GGUF
   HF_FILE=gemma-4-26B-A4B-it-qat-uncensored-heretic-UDmerge-Q4_K_XXL.gguf
   MODEL_ALIAS=EZForever/gemma-4-26B-A4B-it-qat-uncensored-heretic-UDmerge-GGUFQ4_K_XXL
   AD_SERVER__HOST_PASSWORD=replace-with-your-private-host-password
   AD_SERVER__PLAYER_PASSWORD=replace-with-your-private-player-password
   ```

   The remaining defaults select llama.cpp, its inference settings and the canonical
   Jinja template, with `reasoning_effort: none` recommended for gameplay (in docker/game.example.yaml).

3. On Linux, first complete the one-time [folder permissions setup](#saved-files).
   On Windows and macOS, Docker creates the folders automatically. Launch:

   ```bash
   docker compose --env-file docker.env up --build
   ```

   Initial startup downloads the images and model. Once cloudflared is connected,
   the game prints a **Share this address with players** banner in the Compose logs.

4. Join using the host password; share the URL and player password with players.
   Ctrl+C stops the stack.

5. To stop it from another terminal in the project folder, run:

   ```bash
   docker compose --env-file docker.env stop
   ```

   Restart with the launch command above. Active games are lost on shutdown;
   models, certificates and transcripts persist.

## Set up once

Copy `docker/deployment.env.example` to `docker.env` (PowerShell: `Copy-Item`;
Linux/macOS: `cp`). Edit **docker.env**: this is the place for deployment settings.
Set different `AD_SERVER__HOST_PASSWORD` and `AD_SERVER__PLAYER_PASSWORD` values.
Keep this file private; it contains passwords and, for OpenAI, your API key.

Choose one backend by setting `COMPOSE_FILE`:

| Backend                          | Value                               |
| -------------------------------- | ----------------------------------- |
| Local llama.cpp on an NVIDIA GPU | `compose.yaml\|compose.llama.yaml`  |
| Remote OpenAI API                | `compose.yaml\|compose.openai.yaml` |

`compose.yaml` runs the game and defines
the optional tunnel. `compose.llama.yaml` adds the local AI server and its model
settings. `compose.openai.yaml` connects the game to OpenAI instead.
`compose.debug.yaml` adds raw-response diagnostic logging. Select exactly one backend;
do not combine the llama and OpenAI files.

Keep `COMPOSE_PATH_SEPARATOR=|`. For raw response logging, append
`|compose.debug.yaml` to the selected value. For a temporary public link, set
`COMPOSE_PROFILES=tunnel`; otherwise leave it empty. The same commands work for
every combination.

For local llama.cpp, set `HF_REPO` and the exact `HF_FILE`. llama.cpp downloads
the GGUF on first launch and caches it in a volume. Public models need no `HF_TOKEN`;
gated/private models need an authorized token and any required license acceptance.

Recommended for **16 GB VRAM**:

```dotenv
HF_REPO=EZForever/gemma-4-26B-A4B-it-qat-uncensored-heretic-UDmerge-GGUF
HF_FILE=gemma-4-26B-A4B-it-qat-uncensored-heretic-UDmerge-Q4_K_XXL.gguf
MODEL_ALIAS=EZForever/gemma-4-26B-A4B-it-qat-uncensored-heretic-UDmerge-GGUFQ4_K_XXL
```

No model is selected automatically. These weights occupy about 14.3 GB; context,
KV cache and runtime allocations also need memory. The NVIDIA argument list
preserves the supplied launch settings, including 131,072-token context fitting;
that context is not guaranteed to fit every 16 GB GPU. The included canonical
Jinja template permits thinking; the game defaults to `reasoning_effort: none`,
recommended for gameplay.

For OpenAI, set `OPENAI_MODEL` and `AD_OPENAI_API_KEY` in docker.env. API use is
charged to your OpenAI account. This selection starts only
the game (and optional tunnel), with no llama service, model download or model
volume. You can switch providers using the same built image.

## Start, connect and stop

```bash
docker compose --env-file docker.env config --quiet # validate
docker compose --env-file docker.env up --build     # build and start
docker compose --env-file docker.env down           # remove containers; retain data
```

Open **https://localhost:4141**. Direct access uses a self-signed certificate;
trust it or accept your browser's certificate exception before signing in.
Startup waits for llama readiness, then the game's HTTPS health check.
`MODEL_STARTUP_TIMEOUT=3600s` allows an hour for model download/loading, plus six
health failures ten seconds apart. Increase it for slow downloads.

Launch runs in the foreground; Ctrl+C gracefully stops the stack. The game queries
cloudflared's private `/ready` and `/quicktunnel` endpoints during startup, then
prints the connected URL in its own logs. No host launcher script is needed.
Later runtime logs may scroll the banner; run management commands from a second terminal.

The cloudflared message about a missing default configuration file is expected:
Quick Tunnel settings are supplied through Compose command arguments.

Share the printed `https://....trycloudflare.com` URL. It needs no account,
domain or router changes. Passwords still protect the game. Quick Tunnel URLs
change after recreation and have no uptime guarantee. HTTPS and WebSockets use
the public hostname; the tunnel also verifies HTTPS to the game. Restart the
tunnel after replacing its origin certificate.

```bash
docker compose --env-file docker.env logs -f
docker compose --env-file docker.env stop
```

`stop` and ordinary `down` preserve volumes. Restarting loses the active game
and public History; HTML transcripts persist. One container runs one
game process; do not scale the game service.

`stop` retains containers; `down` removes containers and networks. Both preserve
host folders and named volumes. `down --volumes` also deletes named volumes.

## Saved files

Host paths, relative to the project root:

- `data/logged_games/`: HTML transcripts, including hidden guidance and rolls.
- `data/debug/`: raw AI logs, mounted only in debug mode.

Both are excluded from Git and image builds. Sharing transcripts with players after
a session is encouraged. If you plan to reuse the same hidden instructions, review
transcripts and debug logs before sharing to preserve the surprises.

Models and certificates stay in Docker-managed volumes (`anyworld_models` and
`anyworld_certificates`). Open Docker Desktop's **Volumes** view to inspect them.
The private certificate key is readable only by the game container's user.

On **Linux**, run these commands once before launching, from the project folder,
so the game's non-root user (UID 10001) can write its files:

```bash
mkdir -p data/logged_games data/debug
sudo chown -R 10001:10001 data/logged_games data/debug
sudo chmod -R u+rwX,go-rwx data/logged_games data/debug
```

Reading these folders on Linux may require administrator access. Docker Desktop
on Windows and macOS normally requires no ownership changes.

If upgrading from the earlier named-volume setup, copy your existing transcripts
and debug logs out of the old game container **before** recreating it:

```bash
docker compose --env-file docker.env stop
docker compose --env-file docker.env cp game:/app/.logged_games ./data/logged_games
# Only if the old container had debug logging enabled:
docker compose --env-file docker.env cp game:/app/.debug ./data/debug
```

Create `data` first if it does not exist. On Linux, apply the folder permissions
above after copying. Check the copied files before removing the old volumes.

## Change settings

Stop the stack before editing deployment settings in **docker.env**, then launch
again with `docker compose --env-file docker.env up --build`. To change
backend or debug mode, end the game and run `down` **before** changing
`COMPOSE_FILE`, then run `up --build`. The image and saved volumes remain reusable.

For gameplay settings, copy `docker/game.example.yaml` to `docker/game.yaml`,
set `GAME_CONFIG=./docker/game.yaml` in docker.env, and edit that YAML. It
supports the same options as `config.example.yaml`. Provider, model, passwords,
networking and debug mode come from the deployment configuration; set those
in docker.env instead of duplicating them in the game YAML.

For llama-server tuning, edit the **command argument list** in
`compose.llama.yaml`. Every upstream flag
is accepted; quote numeric/boolean arguments as strings. No shell evaluation or
custom argument parser is involved. These lists are the sole source for server
hyperparameters; no extra override file is needed. Advanced mounts and environment
variables can still use standard Compose overrides.

The local backend discovers the live per-slot context through `/props`. The game
YAML's `context_window_size` is only a local fallback (16,384 by default).
OpenAI uses `openai_context_window_size` (245,760 in the example). This is a
smaller budget to trigger summaries before the higher-price input threshold;
see [the pricing explanation](INSTALL.md#context-size-and-openai-costs).
The game YAML also sets `openai_tokenizer_encoding: auto`: OpenAI uses known
tiktoken mappings, while llama.cpp uses its native tokenizer endpoints. Unknown
OpenAI models retain conservative byte estimates; do not assume a GPT-6 encoding.
Check the chosen model's limit and pricing. Omitting that optional value preserves the older
`context_window_size` behavior. Game requests override output limits, reasoning
effort and thinking/template options. Omitted sampling parameters inherit the
llama-server defaults. NVIDIA mlock uses unlimited memlock and `IPC_LOCK` only
in the llama container.

## LAN, VPN and direct internet access

Only the game is published, by default on `0.0.0.0:4141`; llama stays private.
Use `https://localhost:4141` on the host, or its LAN/public/VPN address remotely.
Change `GAME_BIND_ADDRESS` and `GAME_PORT` in docker.env if needed. Add the actual
LAN/public/VPN IPs or hostnames to `TLS_ADDRESSES`, retaining `game`, `localhost`
and `127.0.0.1`. Allow the host port through your firewall. Direct internet access
also needs router forwarding or a VPN; CGNAT may prevent inbound forwarding.
The tunnel works behind CGNAT without inbound port forwarding.

Export the public certificate for clients to trust:

```bash
docker compose --env-file docker.env cp game:/app/certs/cert.pem ./anyworld-cert.pem
```

For supplied certificates, use a Compose bind mount and set the game YAML's
`server.tls_certfile` and `server.tls_keyfile` to their container paths. Their
names must cover the configured addresses, health-check hostname and tunnel
origin hostname; adjust those together if using another name. Keep certificate
verification enabled and the private key unreadable by the tunnel's UID 65532.
Generated keys are mode 0600, owned by game UID 10001. Only the tunnel's fixed
address is trusted for forwarded client addresses. If the ingress subnet
conflicts with your LAN/VPN, change `TUNNEL_SUBNET`, `TUNNEL_IP` and
`GAME_INGRESS_IP` together. Named tunnels and VPN provisioning are outside this setup.

## Upgrade and back up

Images download initially; existing images update only when you explicitly pull.
End the game, back up, then:

```bash
docker compose --env-file docker.env pull
docker compose --env-file docker.env up --build --force-recreate
```

Image variables can use digests for reproducible versions and rollback. Changing
HF repository/file selects another cache entry; refreshing an unchanged model
requires removing just its cached files while llama is stopped.

Stop the game, back up the host `data` folder, and
export the certificates. These commands also work while the stopped container exists:

```bash
docker compose --env-file docker.env stop
mkdir backups
docker compose --env-file docker.env cp game:/app/.logged_games ./backups/logged_games
docker compose --env-file docker.env cp game:/app/certs ./backups/certs
# With debug enabled:
docker compose --env-file docker.env cp game:/app/.debug ./backups/debug
```

Certificate backups include the private key; keep those backups private.
If `backups` already exists, skip `mkdir`. Use `stop`, not `down`, before these
copy commands: copying needs the game container to still exist.
For a full certificate-volume backup, stop the stack and run:

```bash
docker run --rm --user 0 --mount type=volume,source=anyworld_certificates,target=/data,readonly --mount "type=bind,source=${PWD}/backups,target=/backup" --entrypoint tar anyworld-game -czf /backup/certificates.tar.gz -C /data .
```

To restore, copy backed-up transcripts/debug logs into the matching `data`
folders while the game is stopped, then apply Linux permissions if needed.
Restore certificates into the stopped stack's existing volume with:

```bash
docker run --rm --user 0 --mount type=volume,source=anyworld_certificates,target=/data --mount "type=bind,source=${PWD}/backups,target=/backup,readonly" --entrypoint tar anyworld-game -xzf /backup/certificates.tar.gz -C /data
```

Model backups are optional; the same volume commands work with `anyworld_models`.
A different Compose project name changes the volume/image prefix.

**`down --volumes` deliberately deletes certificates and the model cache.**
It leaves the host `data` folders untouched. Delete those folders yourself only
when you intend to remove the archives. Use ordinary `down` for routine shutdown.

## Troubleshooting and checks

Use a second terminal in the project folder:

```bash
docker compose --env-file docker.env ps
docker compose --env-file docker.env logs -f
```

| Problem                                   | What to check                                                                                        |
| ----------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Cannot connect to Docker                  | Start Docker Desktop or the Docker engine; check `docker info`.                                      |
| Required setting or file missing          | Fill both passwords and the backend settings in docker.env. Check file names and relative paths.     |
| Model download fails                      | Check internet access, exact repository/file names and any required HF token or license acceptance.  |
| GPU cannot be used                        | Check the NVIDIA driver and Docker GPU support; Linux also needs NVIDIA Container Toolkit.           |
| Model runs out of memory                  | Reduce context or GPU layers in compose.llama.yaml, or select a smaller model.                       |
| Startup times out                         | Increase `MODEL_STARTUP_TIMEOUT` for slow downloads/loading and check llama logs.                    |
| Tunnel cannot verify the game certificate | Check name coverage and restart the tunnel after certificate replacement. Keep verification enabled. |
| Players cannot reach the direct address   | Check certificate trust and firewall/router/VPN routing, or enable the tunnel.                       |

Avoid sharing `docker compose config` output: it includes resolved secrets.
`config --quiet` safely validates without printing them.

### Developer checks

Checks require a host Python environment with the game dependencies; see
[the Python guide](INSTALL.md#install).

Offline tests resolve all eight NVIDIA/OpenAI, normal/debug and
direct/tunnel combinations. The disposable fake-backend container checks run with:

```bash
python -B docker/smoke.py
```

They verify HTTPS/WebSocket authentication, origin rejection, permissions,
graceful shutdown, archive persistence and debug host folders, then remove their own
containers, volumes and temporary folders. They publish no ports and call no real model.
To also verify the connected URL banner with a live Quick Tunnel and the fake backend, run `python -B docker/smoke.py --tunnel`.
Real inference and full public tunnel checks are separate opt-in acceptance tests:

```bash
python -B docker/live_check.py --model ./models/selected-model.gguf
```

Supply an existing GGUF for the NVIDIA test. It mounts that file read-only and
uses the included canonical template and NVIDIA settings. It creates an
account-free public tunnel with random test passwords, runs a short synthetic
game, checks HTTPS/WebSocket origins and archive finalization, then stops the
tunnel and removes its own containers, volumes and test image. It does not
download a duplicate model or use your deployment credentials.

### Latest acceptance results (2026-10-07)

- NVIDIA: the recommended GGUF and canonical template passed real title,
  opening and round generation on an RTX 5070 Ti with 16 GB VRAM. `/props`
  reported 131,072 tokens. llama.cpp disabled unsupported KV context shifting;
  its fitter also reported that explicit `-ngl 99` prevents automatic layer fitting.
- Public access: account-free Quick Tunnel passed HTTPS, WebSocket authentication
  and origin rejection with certificate verification enabled on both HTTPS hops.
  The existing certificate launcher worked; no HTTP bypass was needed.
- All four fake-backend container variants passed shutdown, volume persistence,
  permissions and debug checks. All eight supported Compose combinations resolved.
- 423 Python tests passed; final deployment tests, Black, Flake8 and
  `git diff --check` passed. Paid OpenAI inference was not run without a selected
  test model. All temporary live stacks, tunnels and volumes were removed.
