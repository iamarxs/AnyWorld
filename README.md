# Anyworld

A tabletop adventure where you never have to roll dice or keep score — just write what your
character does. One player (the host) describes the scenario, then everyone takes turns acting in
their own words while an AI weaves every choice into a story that keeps unfolding.

## Features

- Password-protected host and player roles
- Host-created, LLM-titled scenarios and a waiting lobby
- Strict sequential turns with disconnected-player idle injection
- Unblocked party/system chat
- Bounded and automatically compacted LLM history with Pydantic-validated responses
- Non-blocking, escaped HTML transcripts under `.logged_games/`
- DM-selected d100 checks, token estimates, authenticated reconnection, and host retry/end controls

## Configure

Edit `config.yaml` and replace both `null` password values with strong, unique passwords before launching the game. The application refuses to start while either password is unconfigured.

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
    Direct the game fluently and creatively.
```

Set `provider` to `compatible` for a local OpenAI-compatible backend (the default), or to
`openai` to connect directly to OpenAI. For direct OpenAI use, set `api_key` to your OpenAI API
key and set `model_name` to the OpenAI model you want to use; `endpoint` is ignored. For local
backends, `endpoint` must support OpenAI-compatible structured chat completion parsing.
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

Optional limits under `llm` (defaults shown) apply to every model call, including summaries:

```yaml
  initial_output_tokens: 1024
  round_output_tokens: 2048
  dice_output_tokens: 512
  summary_output_tokens: 1024
  token_safety_margin: 256
  request_timeout_seconds: 120.0
  max_retries: 1
```

Choose caps that leave sufficient input capacity within the effective backend context, especially
for large parties. Before a request exceeds its budget, older rounds are merged into separate
durable memory. Failed or oversized summaries leave the original memory intact. Oversized actions
are rejected while retaining the player's turn. An inference or compaction failure pauses the round
with its submitted actions and any existing dice preserved; the host can use **Retry paused round**
or **End game**. Retrying uses the same dice. Chat stays available during inference.

Dice planning includes private guidance, durable facts and recent history so old injuries, obstacles
and secret triggers remain relevant. Public output is instructed to reveal only observable consequences;
direct guidance echoes and explicit hidden-roll disclosures are rejected. This guard is not a guarantee
against every possible paraphrase of a secret.

Pending sockets receive no game broadcasts. Optional `server` admission settings are
`max_pending_connections: 32`, `auth_timeout_seconds: 30.0`, and `max_auth_attempts: 3`.
Reconnects require a per-player token stored in the same browser tab's sessionStorage, in addition
to the password. Keep that browser session to rejoin your character; clearing it loses the token.

## Install (Windows Git Bash)

Python 3.11 or newer is required.

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

Start the separately managed OpenAI-compatible LLM server, then run:

```bash
python app.py
# or, after installation:
anyworld
```

If the host is accessing the game on a local machine, they should open `http://localhost:4141/`.
The first user enters the host password, creates the scenario, and waits for players using the
player password before starting the game.

Other players should connect using the IP of the host machine, either a LAN IP or a WAN IP, in
which case a port forward should probably be configured in the host's router. It is recommended
to remove the port forward after the game session, unless it is intended to leave the game running
unsupervised.

**Security note:** The game generates a self-signed, short-lived TLS certificate so connections can
use encrypted HTTPS connections. api/tls*bootstrap.py takes automatically care of generating these
certificates when they need to be renewed. \*\*\_This will cause web browsers to warn users that their
connection may be insecure as they connect to the host's IP.
However, browsers allow users to ignore this warning and continue to the app anyway.*\*\*

Development reload is available with `python app.py --reload`.

## Credits

Inspired by the game **AI Dungeon**, especially its earlier, free web-based incarnation
**AI Dungeon 2**.

## AI Credits

Qwen 3.8 27b and OpenAI's Luna model assisted in the production of this app.

## Quality checks

```bash
black --check .
flake8 app.py api core logic tests
pytest
```
