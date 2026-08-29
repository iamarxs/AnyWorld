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
- DM-selected d100 checks, host token estimates, reconnection, and host end-game controls

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
documented limit. A good game experience requires a reasonably large context window; 32,768 tokens
is a useful target when the backend supports it, because more history allows the world and story
to remain coherent across rounds. Anyworld uses this value to trim conversation history, but it
cannot increase the limit enforced by llama.cpp or OpenAI. When history reaches 90% of the
available input budget, older rounds are compacted into structured durable memory; if compaction
fails, the existing oldest-history trimming is used instead. Set `tokenizer_encoding` to the
[tiktoken](https://github.com/openai/tiktoken) encoding used by the configured model so
context-window accounting remains exact. The default (`cl100k_base`) works for many
OpenAI-compatible models. If the reported token counts don't make sense, or the app
errors out on startup, set it to `null` in `config.yaml` to disable tiktoken and fall back to a
rough character-based estimate.

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

**Security note:** To maintain mobile browser compatibility, the initial login sends the raw
password in plaintext rather than a client-bound digest. This is a deliberate trade-off favoring
broad browser support over encrypted transport. When playing without HTTPS/WSS, the password is
visible to anyone observing network traffic, so the server should not be left running unsupervised,
especially if it is exposed to the internet. A solution for this is planned.

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
