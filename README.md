# Artificial Dungeon

Artificial Dungeon is an asynchronous multiplayer text RPG. FastAPI and WebSockets enforce
joining order and turn order, while an OpenAI-compatible LLM generates the scenario and resolves
each round into validated structured output.

## Features

- Password-protected host and player roles
- Host-created, LLM-titled scenarios and a waiting lobby
- Strict sequential turns with disconnected-player idle injection
- Unblocked party/system chat
- Bounded LLM history and Pydantic-validated responses
- Non-blocking plain-text transcripts under `.logged_games/`

## Configure

Edit `config.yaml`:

```yaml
server:
  host: "0.0.0.0"
  port: 4141
  host_password: "admin"
  player_password: "play"
  max_players: 6
llm:
  endpoint: "http://localhost:8033/v1"
  api_key: "sk-no-key-required"
  context_window_size: 128000
  tokenizer_encoding: "cl100k_base"
  model_name: "local"
  system_prompt: >
    Direct the game fluently and creatively.
```

The endpoint must support OpenAI-compatible structured chat completion parsing. Set
`tokenizer_encoding` to the [tiktoken](https://github.com/openai/tiktoken) encoding used by the
configured model so context-window accounting remains exact.

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
artificial-dungeon
```

Open `http://localhost:4141/`. The first user enters the host password, creates the scenario,
and waits for players using the player password before starting the game.

Development reload is available with `python app.py --reload`.

## Quality checks

```bash
black --check .
flake8 app.py api core logic tests
pytest
```
