# Anyworld (ArtificialDungeon): current maintenance guide

This section describes current implementation; differences from the original requirements are not
automatically approved product changes. Proposed fixes belong in TASKS.md.

## Working conventions

- Preserve user edits and ignore `venv/` and `.venv/` in reviews/searches.
- Read-only reviews may run offline tests when temporary files are acceptable. Fixtures remove
  test artifacts on teardown; in-process ASGI tests are allowed, but do not launch a live server
  or run live inference/benchmarks. Edit documentation only when explicitly requested.
- Keep party chat outside LLM context. Private DM guidance and hidden dice stay out of public
  events; server-side HTML transcripts include them, and private-check rolls appear in server logs.
  Treat these files as private archives, not player-safe exports.
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
- Host authentication precedes scenario setup. `generate_scenario_title()` returns only a title
  through ScenarioTitle; it does not remember narrative. Joining players see the host-typed prompt.
  Start Game calls `generate_start_state()` with joined names and broadcasts the generated opening.
  Missing player names are rejected; role, goal, and prose coherence remain prompt instructions.
  No generation occurs just because a player joins.
- Client envelope: event_type plus object data. Events: auth, chat, action, scenario_init,
  start_game, end_game, retry_round. Auth sends name and SHA-256 password_digest of password +
  client ID. Reauthentication additionally requires the private reconnect_token from auth_ok,
  retained in browser sessionStorage. The ID/token pair is also saved per name in localStorage
  for recovery after re-entering name/password at the same origin; passwords/digests are not stored
  there. Pending sockets cannot subscribe, replace a player or act.
- Server envelope: type plus object payload. Types: state_update, chat_echo, turn_directive,
  error, system_msg, auth_ok, scenario_ready, round_start, action_echo, player_roster,
  dm_thinking, game_ended, token_usage. Turn directives use active_player_id.
- `core/schemas.py`: RoundResolution player_resolutions keys must be exact player names; runtime
  validation rejects other keys. ScenarioTitle contains only title. DicePlan has rolls and hidden_rolls.
  ContextSummary has world_state, player_states, important_npcs and unresolved_threads. Models forbid extra fields and coercion.
- `logic/dice.py` generates integers 0..100 inclusive. Do not silently change the probability
  distribution.
- Disconnected players get idle actions when progression is possible; departure/return annotations
  inform the LLM, and persistently absent players are omitted from later outcomes.
- `logic/transcript.py` writes escaped HTML under .logged_games/YYYY-MM-DD-title[-suffix].html,
  not TXT. Appends are thread-offloaded; directory creation at construction is synchronous.
  Original scenario prompt and Opening scenario are separate sections; private guidance and hidden
  checks are included. Token usage is broadcast to all after rounds.
  Writes/finalization are serialized; cancellation waits for outstanding file writes.
- Inference runs as an owned task outside socket receive loops. Generation IDs prevent stale
  commits. Failed rounds pause with actions/dice intact; the host can retry or end. Reconnection
  resumes empty active turns; versioned presence transitions survive older round completions.

## Context and cache guidance

Requests contain system prompt, fixed scenario/private guidance, separate durable memory, recent
history and current input. Dice planning receives the same authoritative context plus the public
state. Compaction budgets the upcoming request and merges older rounds into memory transactionally;
failed, empty or expanding summaries preserve the original context. There is no FIFO-forget fallback.
Normally there are two inference calls per round. Compaction adds a summary and a separate fact
audit; rejected summaries or audits preserve original memory. Tokenizer HTTP calls are not inference.

Every generation has a configured output cap, schema/framing allowance and safety margin. llama.cpp
uses /apply-template and /tokenize when available; OpenAI uses a matching known tiktoken encoding.
Unavailable/unknown tokenization uses a conservative UTF-8-byte estimate. Estimates are labelled;
backend-specific template variations still require an appropriate configured safety margin.
Aggregate actions are preflighted before acceptance. Summary requests are also bounded. Direct
private-guidance echoes and explicit hidden-dice disclosures are rejected before remembering output;
this guard cannot prove arbitrary paraphrases secret-free. No application cache routing exists.

Optional llm settings: initial_output_tokens (1024), round_output_tokens (2048), dice_output_tokens
(512), summary_output_tokens (1024), token_safety_margin (256), request_timeout_seconds (120.0),
max_retries (1), enable_thinking (null), planner_system_prompt (null), debug_raw_responses (false),
compaction_target_fraction (0.75), history_round_limit (null). Title generation caps output at
min(128, initial_output_tokens). Server admission settings: max_pending_connections (32),
auth_timeout_seconds (30.0), max_auth_attempts (3). Parent inference jobs also have a bounded overall deadline.

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

## Diagnostics and display counts

`refresh_usage()` counts retained messages through the request tokenizer without a schema allowance
or generation call. `usage_snapshot()` reports that measurement only while the messages match,
otherwise a labelled local estimate. Retained context excludes the next input/schema/output and
is not round/game consumption. `/props` per-slot n_ctx overrides the configured fallback; the UI
shows the source. Failed backend tokenization remains retryable; never clamp estimates to hide an
overflow or claim byte estimates are exact tokens.

INFO logs cover compaction passes/audits/rollback, inference jobs/retries, accepted actions, auth,
transcript writes and game lifecycle. Private-guidance checks log player names, dice values and
retry reuse on the server. Optional raw responses go to `.debug/llm/` before parsing; these files
can include secrets and are not automatically rotated. See INSTALL.md for launch flags.

## UI

Current desktop columns are 20% chat / 80% game, with 3% title / 92% combined log / 5% input rows.
Mobile <=700px stacks title/log/chat/input. The log is capped at 500 DOM entries (chat at 300),
and snapshots do not replay full history. Host-typed scenario prompt precedes the generated
Opening scenario. Snapshots retain the opening separately from the latest round state.

For normal implementation work: `black --check app.py api core logic tests`,
`flake8 app.py api core logic tests`, and `pytest`. Use fake resolvers and temporary transcripts.
Tests run in disposable working directories and delete transcripts, debug logs, and other temporary
files on teardown, including after failures. They may run in read-only reviews when temporary files
are acceptable. Use `python -B -m pytest -p no:cacheprovider` and set `PYTHONDONTWRITEBYTECODE=1`
to avoid bytecode/cache artifacts in parent and child processes. Forced termination may prevent
cleanup. Fixtures isolate settings and use fake clients; live backend benchmarks remain opt-in.
The initial 2026-09-14 review did not execute tests. The subsequent P1 implementation adds regression
coverage for budgets, memory, privacy, authentication, cancellation and reconnects; see TASKS.md.

## Documentation

README.md is the player-facing overview. INSTALL.md contains installation, configuration,
network/certificate details, checks and diagnostics. TASKS.md distinguishes open work from
historical fixes and local benchmark observations; do not add links to unpublished benchmark files.
