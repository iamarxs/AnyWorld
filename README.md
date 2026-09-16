# Anyworld

![Anyworld banner](static/media/AnyworldBanner.jpg)

A tabletop adventure where you never have to roll dice or keep score — just write what your
character does. One player (the host) describes the scenario, then everyone takes turns acting in
their own words while an AI weaves every choice into a story that keeps unfolding.

## Features

- Local-first AI. [Llama.cpp](https://github.com/ggml-org/llama.cpp) was used during development
- Password-protected host and player roles
- Host-created, LLM-titled scenarios and a waiting lobby
- Actions collected in join order, then resolved together as one simultaneous round
- Automatic idle actions for disconnected players when the round can progress
- Party/system chat available during inference; party chat stays outside the LLM context
- Bounded and automatically compacted LLM history with Pydantic-validated responses
- Non-blocking, escaped HTML transcripts under `.logged_games/`
- DM-selected d100 checks, token accounting, authenticated reconnection, and host retry/end controls
- Responsive dark UI with a scrolling game banner, player roster, party chat, and game log

## Configure

Edit `config.yaml` in the project directory before launching. Set `host_password` and
`player_password` to distinct, nonempty passwords; replace any existing example values as well.
The application rejects missing or identical passwords, but does not enforce password strength.
The `null` values below are placeholders, not usable credentials.

The following is a configuration outline. Keep the full game instructions in the existing
`system_prompt` when changing connection settings; the short example here is not a replacement
for those instructions.

```yaml
server:
  host: "0.0.0.0"
  port: 4141
  host_password: null
  player_password: null
  max_players: 6
llm:
  provider: "compatible" # use "openai" for the direct OpenAI API
  endpoint: "http://localhost:8033/v1"
  api_key: "sk-no-key-required"
  context_window_size: 8192 # fallback; llama.cpp auto-detection takes precedence
  tokenizer_encoding: "cl100k_base"
  model_name: "local"
  system_prompt: >
    Direct a coherent multiplayer RPG. Resolve actions simultaneously, preserve established
    facts and exact player names, and reserve dice for meaningful risks. Keep private
    guidance secret. Return only the requested structured object.
```

Set `provider` to `compatible` for a local OpenAI-compatible backend (the default), or to
`openai` to connect directly to OpenAI (OpenAI backend not tested!).
For direct OpenAI use, set `api_key` to your OpenAI API key and set `model_name` to the OpenAI
model you want to use; `endpoint` is ignored. For local backends, `endpoint` must support
OpenAI-compatible structured chat completion parsing.
`context_window_size` is an optional fallback value. When using a compatible backend, Anyworld
still attempts to read the context size from the llama.cpp `/props` endpoint even when a value is
configured. A successful discovery takes precedence; if discovery is unavailable, the configured
value is used. The default fallback is a conservative 8,192 tokens. OpenAI does not currently
provide automatic context-limit discovery, so configure this value to the selected model's
documented limit. The configured value cannot increase the backend's actual capacity.
llama.cpp token counting uses its `/apply-template` and `/tokenize` endpoints when available.
For direct OpenAI, a configured `tokenizer_encoding` is used only if it matches the model's known
tiktoken encoding. Unknown models, mismatches, unavailable endpoints, or `null` encoding use a
conservative UTF-8-byte estimate. Schema/framing allowances and a safety margin are added; counts
are not advertised as exact. The token indicator's tooltip describes the counting method.

Optional limits under `llm` (defaults shown) bound model calls. Output caps are selected by
request type; memory audits share the summary output cap:

```yaml
initial_output_tokens: 1024
round_output_tokens: 2048
dice_output_tokens: 512
summary_output_tokens: 1024
token_safety_margin: 256
request_timeout_seconds: 120.0
max_retries: 1
```

Additional optional `llm` settings:

| Setting                      | Default | Behavior                                                                                                                                                                       |
| ---------------------------- | ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `enable_thinking`            | `null`  | Sends `chat_template_kwargs.enable_thinking` only to compatible backends when set. Support depends on the backend/template; the current `config.yaml` sets it to `false`.      |
| `planner_system_prompt`      | `null`  | Replaces the system prompt for dice planning only. Scenario, private guidance, memory, and recent history are still supplied.                                                  |
| `compaction_target_fraction` | `0.75`  | After compaction starts, aims to leave the upcoming request within this fraction of the context window. Allowed range: `0.5`–`1.0`.                                            |
| `history_round_limit`        | `null`  | Optionally requests earlier memory checkpoints after this many stored request/response pairs, including setup calls. Allowed range: `2`–`100`; this is not a hard history cap. |

Choose caps that leave sufficient input capacity within the effective backend context, especially
for large parties. Before a request exceeds its budget, older rounds are merged into separate
durable memory, followed by a separate model audit of lasting facts. Empty, non-shrinking,
oversized, or rejected summaries leave the original memory intact; old history is not simply
discarded. A model audit reduces risk but cannot guarantee perfect recall. Oversized actions
are rejected while retaining the player's turn. An inference or compaction failure pauses the round
with its submitted actions and any existing dice preserved; the host can use **Retry paused round**
or **End game**. Retrying uses the same dice. Chat stays available during inference.

A normal round uses two generation requests: dice planning and narrative resolution. Compaction
adds summary and audit requests; retries and backend token-counting requests add further work.
The expandable token indicator shows estimated retained context and reported round/game usage,
cache counters, errors, retries, and timing. Missing provider counters appear as unknown. Cached
input still occupies context, and retained context is not the full size of the next request.

Dice planning includes private guidance, durable facts and recent history so old injuries, obstacles
and secret triggers remain relevant. Public output is instructed to reveal only observable consequences;
direct guidance echoes and explicit hidden-roll disclosures are rejected. This guard is not a guarantee
against every possible paraphrase of a secret.

Planning instructions default to no roll for routine observations or searches. A concrete
obstacle, opposition, or hazard can justify a check; atmosphere alone should not. These are
model instructions, not a deterministic classifier, so decisions depend on the backend. Dice
values are generated by the server from 0 through 100 inclusive. Exact repeated player-name
labels are removed from outcomes before display and history storage; arbitrary spelling errors
in generated prose are not automatically corrected.

Pending sockets receive no game broadcasts. Optional `server` admission settings are
`max_pending_connections: 32`, `auth_timeout_seconds: 30.0`, and `max_auth_attempts: 3`.
Reconnects require a private per-player token as well as the password. A disconnected tab
reconnects automatically using its session credentials. If you close the tab or browser, reopen
the **same address** in the **same browser profile** and enter the same player name and password.
After successful login, the browser saves that player's ID and reconnect token in localStorage;
a new tab can restore them after you enter your credentials. Passwords and password digests
are not saved in localStorage. Rejoining replaces the previous socket for that player.

Clearing site storage, using a different browser/profile, or changing the scheme, hostname/IP,
or port loses access to that saved identity. The shared password and name alone cannot reclaim
an existing player. Private browsing or blocked storage may prevent recovery after closing the
tab. Sessions created before this recovery feature need one successful login/reconnect in the
original tab with the updated client before their identity is saved for new-tab recovery.
Reconnect snapshots restore current state and submitted actions, not the entire past log. The UI
keeps at most 500 game-log elements (including the banner) and 300 chat entries. The banner is
the first item in the game pane and scrolls with its contents.

## Install (Linux and Windows)

Clone the repository and change into its directory:

```bash
git clone git@github.com:iamarxs/AnyWorld.git
cd AnyWorld
```

Python 3.11 or newer is required. Run the remaining commands from the repository root. On
Linux, use `python3` and `venv/bin/activate`; on Windows Git Bash, use `py -3.11` and
`venv/Scripts/activate` (or replace `-3.11` with your installed Python version).

Linux:

```bash
python3 -m venv venv
source venv/bin/activate
python -m pip install -e .
```

Windows Git Bash:

```bash
py -3.11 -m venv venv
source venv/Scripts/activate
python -m pip install -e .
```

For formatting, linting, and tests, install the development extras:

```bash
python -m pip install -e '.[dev]'
```

## Run

For `provider: compatible`, start the separately managed LLM server first (llama.cpp is guaranteed
to be compatible, but all OpenAI compatible backends should work. OpenAI or other internet based
backends have not been tested yet). With `provider: openai`, no local LLM server is needed
(untested for now). From the repository root, run:

```bash
python app.py
# or, after installation:
anyworld
```

Open [https://127.0.0.1:4141/](https://127.0.0.1:4141/) on the server machine.
The application serves HTTPS; an `http://` URL will not work with the normal launcher.

1. The first user signs in with the host password and creates a scenario, optionally adding
   private DM guidance.
2. After scenario generation, other players join using the player password. `max_players`
   includes the host.
3. The host starts the game, which generates introductions for the joined players.
4. Players submit actions in join order. Once the round's actions are collected, the DM resolves
   them together. Use party chat independently of action submission.
5. The host can retry a paused round or end the game. HTML transcripts are written to
   `.logged_games/YYYY-MM-DD-title[-suffix].html`; hidden dice and private guidance are excluded
   from the transcript inputs.

Other players connect to `https://<server-IP>:4141/` using the server's LAN address, or its public
address for internet play. Allow the configured TCP port through the firewall; internet play may
also require router port forwarding. Remove temporary forwarding when the session ends.

### Recommended models

The game was developed using **Gemma 4**-26B-A4B-it (Q4_K_M or similar quant) as the backend's model,
with a context size of **128k**, which was determined to be sufficiently intelligent and creative
to act as the DM for the game. Use the best quantization you can while preserving a long enough
context for longer games. The game does intelligently compact the context when it reaches a certain
fill ratio, but no less than 64k is recommended.

For Gemma 4 models, these settings are recommended:

- temperature 1.0
- top-p 0.95
- top-k 20
- min-p 0.0
- presence-penalty 0.0
- repeat-penalty 1.0

### HTTPS certificates

`api/tls_bootstrap.py` creates `certs/cert.pem` and `certs/key.pem`. It attempts public-IP
discovery via external services, falling back to a local interface address. Certificates cover
that detected IP and `127.0.0.1`, last 825 days, and are regenerated at startup when missing,
within 30 days of expiry, or no longer covering the detected IP. They do not include the
`localhost` hostname or every LAN address.

Browsers will warn because the certificate is self-signed, and may also report an address
mismatch when using another address. For a server you recognize and trust, use the browser's
certificate exception if available, or deploy a trusted certificate/reverse proxy.

### Server lifecycle

One server process hosts one in-memory game. Multiple workers and concurrent independent games
are not supported. Restarting loses the live session; the HTML transcript is a record, not a
loadable save. After ending a game, restart the server to begin another.

Restart after configuration changes. CLI overrides are available as `--host` and `--port`, for
example `anyworld --host 0.0.0.0 --port 4141`. Development reload is available with
`python app.py --reload`; code-triggered reloads also reset the in-memory game.

## TODO

- OpenAI backend has not been tested yet, functionality not guaranteed. llama.cpp was used for development.

## Credits

Inspired by the game **AI Dungeon**, especially its earlier, free web-based incarnation
**AI Dungeon 2**.

## AI Credits

Alibaba Cloud's Qwen 3.8 27b and OpenAI's GPT-5.6 Luna and GPT-6 Astra models
assisted in the production of this app.

## Quality checks

```bash
black --check app.py api core logic tests
flake8 app.py api core logic tests
pytest
# Client reconnect and password-hashing regressions (requires Node.js):
node --test tests/client_reconnect.test.cjs
```

Tests use isolated settings and fake model clients; they do not require a running LLM. They
can create temporary transcripts and `.logged_games/`, so they are not strictly read-only checks.

## Raw model diagnostics

To investigate generated wording, launch with `python app.py --debug` or
`anyworld --debug-raw-responses`. The flag also works with `--reload` and enables logging
for that launch without changing `config.yaml`. Alternatively, set `debug_raw_responses: true`
under `llm` in the configuration. Logging is disabled by default. Restarting the application
resets the current game. Each completion HTTP response is saved as a timestamped JSON file under
`.debug/llm/` in the working directory. The `body` field contains the raw response text,
recorded before SDK parsing, narrative checks, name normalization, or display. This includes
responses rejected during retries and HTTP error responses. Connection failures with no HTTP
response cannot produce a raw-response file.

These files are private diagnostics: model output can include hidden dice or private guidance,
and error bodies can contain sensitive data. They are excluded from Git and are not served by
the web app. Request prompts, headers, and credentials are not deliberately logged. Files are
not automatically rotated; disable the option after diagnosis and remove unneeded logs.
Sampling settings remain controlled by the backend; Anyworld does not override repetition or
presence penalties.
