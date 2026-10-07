# Anyworld (ArtificialDungeon): current maintenance guide

This guide is for developers and coding assistants. For playing, use [README.md](README.md).
For setup, use [INSTALL.md](INSTALL.md) or [DOCKER.md](DOCKER.md).

It describes the current code. A difference from older requirements does not give permission
to change the product. Record proposed fixes in TASKS.md.

## Working conventions

- Preserve user edits and ignore `venv/` and `.venv/` in reviews/searches.
- Read-only reviews may run offline tests when temporary files are acceptable. Fixtures remove
  test artifacts on teardown; in-process ASGI tests are allowed, but do not launch a live server
  or run live inference/benchmarks. Edit documentation only when explicitly requested.
- Keep party chat outside LLM context. Private DM guidance and hidden dice stay out of public
  events; server-side HTML transcripts include them, and private-check rolls appear in server logs.
  This separation preserves surprises during play. Encourage sharing HTML transcripts
  after a session; advise checking them only if the host wants to preserve hidden
  instructions for reuse. Credential and private-key handling remain separate.
- Preserve join-order input collection and simultaneous, causally coherent round resolution.
- Keep network/file I/O outside state-mutation locks; use logic/models.py protocols rather than
  importing concrete transport into game logic. An ended game must not be revived by stale inference.

## Current architecture and contracts

- Python 3.11+, FastAPI, Pydantic, vanilla JS/CSS. Package and CLI name: `anyworld`.
- `app.py` validates passwords and starts Uvicorn with HTTPS. `api/tls_bootstrap.py` handles
  IP discovery and self-signed certificates under `certs/`. Explicit `tls_addresses` skip
  discovery. Generated certificates also cover localhost, IPv4 loopback and IPv6 loopback.
- `core/config.py` exports lowercase singleton `settings`. The llm schema additionally contains
  provider (`compatible`/`openai`), tokenizer_encoding, openai_tokenizer_encoding, system_prompt and shared
  `reasoning_effort`. Compatible `/props`
  discovery overrides context_window_size when successful. OpenAI uses the optional
  openai_context_window_size instead, falling back to context_window_size if omitted/null.
  Examples use 245760 to leave room below long-context pricing. Server passwords must be distinct.
  Settings merge `AD_` environment overrides over YAML, using `__` for nested fields;
  `AD_SERVER__HOST_PASSWORD` and `AD_SERVER__PLAYER_PASSWORD` override YAML credentials.
  Project `.env` values load without overwriting process variables. Direct OpenAI uses only
  `AD_OPENAI_API_KEY`; `config.example.yaml` is a manual template, not an automatic source.
- `LLMContextManager._create_client()` clears SDK organization/project after construction so
  inherited `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID` do not scope game requests. Passing None to
  the SDK constructor alone still reads these variables. Leave the process environment unchanged.
  OpenAI narration follows the scenario's language; compatible-provider narration requests English.
- `api/server.py` owns GET /, /static, /ws/{client_id}, ConnectionManager and an engine/resolver
  per ASGI lifespan. Lifespan validates passwords and closes sockets, tasks and clients.
  Host-only `new_game` replaces an ENDED engine with a fresh engine/resolver, retaining only
  the authenticated host's identity/token. Prior player sockets close with 4002 and require
  explicit rejoining after scenario setup; pending sockets authenticate against the current engine.
  Old rejected handlers recheck engine/socket ownership before replying. The old resolver closes;
  the new game owns a distinct public journal and creates a separate HTML transcript on Start.
  Client IDs must be canonical UUIDs. No multi-session or multi-worker coordination exists.
- `logic/models.py` contains GameState (including ENDED), Player and dependency protocols.
  `logic/lobby.py` owns authentication, scenario setup, start/end, chat and reconnect snapshots.
  `logic/engine.py` owns the lock, turn deque, action buffer and round orchestration.
- `logic/validation.py` validates text; `logic/presentation.py` normalizes player outcome names.
- `logic/dice.py` validates the dedicated chance-event field and combines it with freeform private
  guidance; percentage rolls remain server-authoritative and private. Unqualified per-round
  checks are synthesized by Python; conditional triggers and optional eligibility use a focused
  LLM pass. Cadence, eligibility and shared/per-player scope are independent settings.
  Planning audits run only for conditional triggers or hidden action checks, before any rolls;
  server-generated per-round checks are excluded. Hidden sources use schema-constrained guidance
  lines. A hidden label without a valid non-percentage guidance source is normalized to public
  while preserving the planned action roll, avoiding a retry loop over privacy classification.
- Host authentication precedes scenario setup. `generate_scenario_title()` returns only a title
  through ScenarioTitle; it does not remember narrative. Joining players see the host-typed prompt.
  Scenario setup accepts one optional `StructuredChanceRule` in `chance_rule`, plus separate
  freeform `guidance`. The rule has percentage (0–100), cadence, trigger, eligibility, effect
  and scope. Conditional cadence requires a trigger; per-round cadence forbids one.
  The old `chance_event` field and legacy text form are removed. Percentage rules in freeform
  guidance are rejected, preserving one event per game. The validated rule is serialized
  into the resolver's private context.
  Start Game calls `generate_start_state()` with joined names and broadcasts the generated opening.
  Missing player names are rejected; role, goal, and prose coherence remain prompt instructions.
  Setting-conflicting attempts may receive a public difficulty roll when their outcome is
  uncertain; they are not rejected solely because the requested target seems impossible.
  No generation occurs just because a player joins.
- Client envelope: event_type plus object data. Events: auth, chat, action, scenario_init,
  start_game, end_game, new_game, retry_round, journal_request. Auth sends name and SHA-256 password_digest
  of password + client ID. Reauthentication additionally requires the private reconnect_token from auth_ok,
  retained in browser sessionStorage. The ID/token pair is also saved per name in localStorage
  for recovery after re-entering name/password at the same origin; passwords/digests are not stored
  there. Pending sockets cannot subscribe, replace a player or act.
- Server envelope: type plus object payload. Types: state_update, chat_echo, turn_directive,
  error, system_msg, auth_ok, scenario_ready, round_start, action_echo, player_roster,
  dm_thinking, game_ended, token_usage, journal_page, action_accepted.
  Turn directives use active_player_id.
- `core/schemas.py`: RoundResolution player_resolutions keys must be exact player names; runtime
  validation rejects other keys. Opening generation uses an empty player_resolutions object.
  ScenarioTitle contains only title. DicePlan has rolls and hidden_rolls.
  ContextSummary has world_state, player_states, important_npcs and unresolved_threads. Models forbid extra fields and coercion.
- `logic/dice.py` action dice use integers 0..100 inclusive. Percentage-event dice use 1..100
  inclusive and succeed at roll <= chance_percent, giving exact 0% and 100% boundaries.
  Do not silently change either probability distribution.
- Disconnected players get idle actions when progression is possible; departure/return annotations
  inform the LLM, and persistently absent players are omitted from later outcomes.
- `logic/transcript.py` writes escaped HTML under .logged_games/YYYY-MM-DD-title[-suffix].html,
  not TXT. Appends are thread-offloaded; directory creation at construction is synchronous.
  Original scenario prompt and Opening scenario are separate sections; private guidance and hidden
  checks are included. Private DM guidance has separate Freeform guidance and Chance event
  subtitles; structured rules render labeled, human-readable settings. There is no legacy
  text fallback. The resolver still receives the original combined private context.
  Token usage is broadcast to all after rounds.
  Writes/finalization are serialized; cancellation waits for outstanding file writes.
- `logic/journal.py` keeps allowlisted public events only in memory for the current game,
  with session IDs and event cursors for reconnect replay, paginated history, search and export.
  It excludes private guidance/rolls and never creates or writes `.public_games/` files. Browser
  History export still downloads JSONL; a new game or server restart clears public history.
  Pages retain the 512 KiB response budget and the `incomplete: false` compatibility field.
- Inference runs as an owned task outside socket receive loops. Generation IDs prevent stale
  commits. LLM round failures receive up to two automatic retries within the existing job deadline,
  reusing pending actions/dice. Exhausted failures pause; the host can retry or end. Reconnection
  resumes empty active turns; versioned presence transitions survive older round completions.

## Context and cache guidance

Requests contain system prompt, fixed scenario/private guidance, separate durable memory, recent
history and current input. Dice planning receives the same authoritative context plus the public
state. Compaction budgets the upcoming request and merges older rounds into memory transactionally;
failed, empty or expanding summaries preserve the original context. There is no FIFO-forget fallback.
Rounds use dice planning and narrative resolution; conditional chance triggers or eligibility
can add a focused trigger pass, and hidden/conditional planning may require audits.
Compaction adds a summary and a separate fact audit; rejected summaries or audits preserve
original memory. Tokenizer HTTP calls are not inference.

Every generation has a configured output cap, schema/framing allowance and safety margin. llama.cpp
uses /apply-template and /tokenize when available; OpenAI uses a matching known tiktoken encoding.
`openai_tokenizer_encoding: auto` resolves known model mappings; omitted/null preserves the
legacy `tokenizer_encoding` behavior. Unknown models and mismatches retain byte estimates.
Unavailable/unknown tokenization uses a conservative UTF-8-byte estimate. Estimates are labelled;
backend-specific template variations still require an appropriate configured safety margin.
Aggregate actions are preflighted before acceptance using the live dice and baseline resolution
prompt builders; the final resolution prompt is measured again after dice and chance-event context
are available. Summary requests are also bounded. Direct private-guidance echoes and explicit
hidden-dice disclosures are rejected before remembering output during play;
this guard cannot guarantee the model will preserve every intended surprise. No application cache routing exists.

Optional llm settings: initial_output_tokens (4096), round_output_tokens (4096), dice_output_tokens
(768), summary_output_tokens (3072), token_safety_margin (256), request_timeout_seconds (120.0),
max_retries (1), reasoning_effort (none/low/medium/high),
debug_raw_responses (false),
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

Readable raw diagnostics use .log files; requests belonging to a round may first use .tmp files
and are combined when round diagnostics are finalized.

INFO logs cover compaction passes/audits/rollback, inference jobs/retries, accepted actions, auth,
transcript writes and game lifecycle. Private-guidance checks log player names, dice values and
retry reuse on the server. Optional raw responses go to `.debug/llm/` before parsing; these files
include hidden gameplay guidance and are not automatically rotated. See INSTALL.md for launch flags.

## UI

Current desktop columns are 20% chat / 80% game, with 3% title / 92% combined log / 5% input rows.
Mobile <=700px stacks title/log/chat/input. The log is capped at 500 DOM entries (chat at 300),
and snapshots retain the opening separately from the latest round state. Reconnect catch-up uses
public journal cursors rather than embedding full history in snapshots; History provides search
and JSONL export. Host-typed scenario prompt precedes the generated Opening scenario.
After ENDED, only the host sees Start new game. Session changes clear chat/log entries,
History filters/cursors, export buffers, and player colors. Saved action drafts carry a session ID;
same-game reloads preserve them, while different or unscoped drafts are cleared on authentication.
The chance section is labeled "Chance-based event rule (optional)"; there is no legacy text input.
The host card is min(48rem, 100%) wide, with chance-control labels above their own inputs in
two columns, stacking into one column at <=700px. Inputs can shrink. Chance rules are validated
on scenario submission; the host form has no JSON preview. Freeform DM guidance remains separate.
Script/CSS URLs use manual cache versions in
`templates/index.html`; bump the relevant version when changing an asset. The client uses
ws/wss according to page protocol and the fixed `/ws/{client_id}` route.

For normal implementation work: `black --check app.py api core logic tests`,
`flake8 app.py api core logic tests`, and `pytest`. Use fake resolvers and temporary transcripts.
Tests run in disposable working directories and delete transcripts, debug logs, and other temporary
files on teardown, including after failures. They may run in read-only reviews when temporary files
are acceptable. Use `python -B -m pytest -p no:cacheprovider` and set `PYTHONDONTWRITEBYTECODE=1`
to avoid bytecode/cache artifacts in parent and child processes. Forced termination may prevent
cleanup. Fixtures isolate settings and use fake clients; live backend benchmarks remain opt-in.
The initial 2026-09-14 review did not execute tests. The subsequent P1 implementation adds regression
coverage for budgets, memory, privacy, authentication, cancellation and reconnects; see TASKS.md.

## Docker deployment

`compose.yaml` provides the HTTPS game and optional account-free Quick Tunnel.
`compose.llama.yaml` adds NVIDIA CUDA llama.cpp, native HF downloads and readiness ordering;
CPU inference is not supported. `compose.openai.yaml` selects the remote API and defines no
local backend or model cache. `compose.debug.yaml` enables raw diagnostics in the host `data/debug/` folder.
`docker.env` selects the files/profile and deployment settings. Game YAML is mounted read-only;
model-server flags live in the local backend's Compose argument list. The canonical Jinja file
is in `docker/templates/`; it permits thinking. Generated certs and model cache persist in named
volumes; transcripts and diagnostics use host `data/logged_games/` and `data/debug/` bind mounts; active game state/public History stay in memory. The tunnel
verifies origin HTTPS and can read the public certificate but not the private key. Only its fixed
source address is trusted for forwarded client identity. The game uses an owned, cancellable background task to query cloudflared's private
metrics endpoints and announce the URL after `/ready` succeeds. It waits at most
120 seconds without blocking startup; direct-only deployments remain quiet. See DOCKER.md for operations; docker/smoke.py uses a fake backend, whereas
live_check.py explicitly starts real NVIDIA inference and a temporary public tunnel.

## Documentation

README.md is the player-facing overview. INSTALL.md contains installation, configuration,
network/certificate details, checks and diagnostics. DOCKER.md contains Docker setup and operation.
TASKS.md distinguishes open work from
historical fixes and local benchmark observations; do not add links to unpublished benchmark files.
