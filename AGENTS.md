# Anyworld (ArtificialDungeon): current maintenance guide

This section describes current implementation; differences from the original requirements are not automatically approved product changes. Proposed fixes belong in TASKS.md.

## Working conventions

- Preserve user edits and ignore `venv/` and `.venv/` in reviews/searches.
- Keep read-only reviews free of application startup, inference calls and runtime artifacts;
  edit documentation only when explicitly requested.
- Keep party chat outside LLM context and private DM guidance outside public events/transcripts.
- Preserve join-order input collection and simultaneous, causally coherent round resolution.
- Keep network/file I/O outside state-mutation locks; use logic/models.py protocols rather than
  importing concrete transport into game logic. An ended game must not be revived by stale inference.

## Current architecture and contracts

- Python 3.11+, FastAPI, Pydantic, vanilla JS/CSS. Package and CLI name: `anyworld`.
- `app.py` validates passwords and starts Uvicorn with HTTPS. `api/tls_bootstrap.py` handles
  IP discovery and self-signed certificates under `certs/`.
- `core/config.py` exports lowercase singleton `settings`. The llm schema additionally contains
  provider (`compatible`/`openai`), tokenizer_encoding and system_prompt. Compatible `/props`
  discovery overrides context_window_size when successful. Server passwords must be distinct.
- `api/server.py` owns GET /, /static, /ws/{client_id}, ConnectionManager and an engine/resolver
  per ASGI lifespan. Lifespan validates passwords and closes sockets, tasks and clients.
  Client IDs must be canonical UUIDs. No multi-session or multi-worker coordination exists.
- `logic/models.py` contains GameState (including ENDED), Player and dependency protocols.
  `logic/lobby.py` owns authentication, scenario setup, start/end, chat and reconnect snapshots.
  `logic/engine.py` owns the lock, turn deque, action buffer and round orchestration.
- `logic/validation.py` validates text; `logic/presentation.py` normalizes player outcome names.
- First successful host authentication precedes scenario generation and player joining.
  Starting the game separately generates introductions for the joined players.
- Client envelope: event_type plus object data. Events: auth, chat, action, scenario_init,
  start_game, end_game, retry_round. Auth sends name and SHA-256 password_digest of password +
  client ID. Reauthentication additionally requires the private reconnect_token from auth_ok,
  retained in browser sessionStorage. Pending sockets cannot subscribe, replace a player or act.
- Server envelope: type plus object payload. Types: state_update, chat_echo, turn_directive,
  error, system_msg, auth_ok, scenario_ready, round_start, action_echo, player_roster,
  dm_thinking, game_ended, token_usage. Turn directives use active_player_id.
- `core/schemas.py`: RoundResolution keys are exact player names (engine also accepts a client-ID
  fallback). DicePlan has rolls and hidden_rolls. ContextSummary has world_state, player_states,
  important_npcs and unresolved_threads. Models forbid extra fields and coercion.
- `logic/dice.py` generates integers 0..100 inclusive. Do not silently change the probability
  distribution. Threshold inconsistencies are recorded in TASKS.md.
- Disconnected players get idle actions when progression is possible; departure/return annotations
  inform the LLM, and persistently absent players are omitted from later outcomes.
- `logic/transcript.py` writes escaped HTML under .logged_games/YYYY-MM-DD-title[-suffix].html,
  not TXT. Appends are thread-offloaded; directory creation at construction is synchronous.
  Public dice are recorded, hidden dice are omitted. Token usage is broadcast to all after rounds.
  Writes/finalization are serialized; cancellation waits for outstanding file writes.
- Inference runs as an owned task outside socket receive loops. Generation IDs prevent stale
  commits. Failed rounds pause with actions/dice intact; the host can retry or end. Reconnection
  resumes empty active turns; versioned presence transitions survive older round completions.

## Context and cache guidance

Requests contain system prompt, fixed scenario/private guidance, separate durable memory, recent
history and current input. Dice planning receives the same authoritative context plus the public
state. Compaction budgets the upcoming request and merges older rounds into memory transactionally;
failed, empty or expanding summaries preserve the original context. There is no FIFO-forget fallback.
Normally there are two inference calls per round plus occasional summaries and tokenizer HTTP calls.

Every generation has a configured output cap, schema/framing allowance and safety margin. llama.cpp
uses /apply-template and /tokenize when available; OpenAI uses a matching known tiktoken encoding.
Unavailable/unknown tokenization uses a conservative UTF-8-byte estimate. Estimates are labelled;
backend-specific template variations still require an appropriate configured safety margin.
Aggregate actions are preflighted before acceptance. Summary requests are also bounded. Direct
private-guidance echoes and explicit hidden-dice disclosures are rejected before remembering output;
this guard cannot prove arbitrary paraphrases secret-free. No application cache routing exists.

Optional llm settings: initial_output_tokens (1024), round_output_tokens (2048), dice_output_tokens
(512), summary_output_tokens (1024), token_safety_margin (256), request_timeout_seconds (120.0),
max_retries (1). Server admission settings: max_pending_connections (32), auth_timeout_seconds
(30.0), max_auth_attempts (3). Parent inference jobs also have a bounded overall deadline.

Token bounds are currently estimates, not guarantees. When improving this subsystem:

- Budget formatted input, schema overhead, output allowance and a safety margin on every call.
  A tiktoken encoding does not guarantee llama.cpp token counts.
- Preserve durable player/world facts separately from disposable prose. Do not silently lose
  injuries, possessions, consequences or unresolved threads during compaction/trimming.
- Keep stable prompt prefixes and serialization stable. Measure compaction savings against lost
  prefix reuse. Distinguish cached token counts, backend KV/prompt caches and cached outcomes.
- Cached input still occupies context. Do not reuse narrative outcomes across different state,
  actions or dice. Record all calls, including summaries, retries and failures.
- Validate coherence and total tokens/latency/cache reuse with the deployed backend. Cache options
  are provider/version-specific; do not claim percentage savings without measurements.

## UI differences and validation

Current desktop columns are 20% chat / 80% game, with 3% title / 92% combined log / 5% input rows.
There is no separate 25% state pane. Mobile <=700px stacks title/log/chat/input. The log is capped
at 500 DOM entries (chat at 300), and snapshots do not replay full history. These differ from the
original separate-state-pane and scroll-to-start requirements; see TASKS.md for reconciliation.

For normal implementation work: `black --check app.py api core logic tests`,
`flake8 app.py api core logic tests`, and `pytest`. Use fake resolvers and temporary transcripts.
Tests can create .logged_games and temporary transcripts, so do not run them in a strictly read-only
review. Fixtures isolate settings and use fake clients; backend benchmarks remain opt-in future work.
The initial 2026-09-14 review did not execute tests. The subsequent P1 implementation adds regression
coverage for budgets, memory, privacy, authentication, cancellation and reconnects; see TASKS.md.
