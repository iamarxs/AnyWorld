# Tasks

This is the developer work list, not a setup guide. Players should use [README.md](README.md);
hosts should use [INSTALL.md](INSTALL.md) or [DOCKER.md](DOCKER.md).

Historical entries may describe issues superseded by later work; verify the current code.
References to private guidance, hidden rolls and player-safe exports describe
in-session visibility. Hidden guidance and chance rules add surprises to play;
sharing full HTML transcripts afterward is encouraged. Check them before sharing
only if the host wants to preserve the same surprises for future sessions.
Credentials and private keys still require protection.

Static review: 2026-09-14, including pre-existing working-tree edits. Implementation was read
only; venv and .venv were excluded. No tests, app startup or live inference were performed.
P1 = correctness/security or substantial waste; P2 = optimization/reliability; P3 = optional.
Active items remain open until their acceptance checks pass. Implementation of the 2026-09-27
review findings was authorized on 2026-09-27; other backlog items remain recommendations.

Current review policy: offline tests may run in read-only reviews when temporary files are
acceptable. Per-test working directories and their artifacts are deleted on teardown, including
after failures; forced termination can prevent cleanup. Use `python -B -m pytest -p no:cacheprovider`
with `PYTHONDONTWRITEBYTECODE=1` to avoid bytecode/cache artifacts, including in child processes.
In-process ASGI tests are allowed; live server startup and backend benchmarks remain outside this
workflow. The no-tests statement above records the historical review, not a current restriction.

## Active

- [ ] **P1 - Preserve cadence conditions in private chance rules (R01)** - logic/dice.py:conditional_chance_rule_ids, normalize_chance_rule_decisions.
  - Per-round wording currently overrides eligibility conditions; unrecognized conditional wording silently becomes per-round. Separate cadence from eligibility or reject ambiguity, preserving one event and both existing RNG distributions.
  - Product follow-up (S01): let the host choose cadence, eligibility, percentage and shared/per-player occurrence explicitly, with a plain-language preview. Show ambiguous conditions before Start rather than silently discarding them.
  - Acceptance: Qualified per-round rules never fire while ineligible; cover mixed clauses, alternate/language wording, shared occurrences and 0/100% boundaries.

- [ ] **P1 - Separate public telemetry from private inference diagnostics (R02)** - logic/llm_manager.py:usage_snapshot; logic/engine.py:\_publish_usage.
  - All players receive round_by_kind and last_request; event_audit reveals that at least one private event occurred. Keep public aggregate usage but project private phase diagnostics to the host only; explicitly decide residual count/timing leakage.
  - Product follow-up (S07): provide a host-only diagnostics panel and a coarse working/paused indicator for players. Label tokenizer estimates separately from provider usage and avoid invented billing precision.
  - Acceptance: Inspect public WebSocket payloads for hidden audit categories, including failed rounds; preserve useful host diagnostics.

- [ ] **P1 - Bound cross-connection authentication and message abuse (R03)** - api/server.py; app.py; core/schemas.py.
  - Per-socket attempts reset on reconnect. Add bounded global/per-source login budgets, configurable Origin validation, per-player message rates and explicit byte/depth limits before expensive parsing. Define proxy trust and avoid attacker-triggered victim lockouts.
  - Include an ASGI-level cap for the first/auth messages and close over-budget WebSocket frames with 1009 where possible; a practical interim auth-message limit is about 64 KiB.
  - Acceptance: Fresh sockets cannot reset guessing limits; flood/large/deep messages cannot starve normal players; NAT and valid reconnects remain usable. Highest urgency for internet-facing deployments.

- [ ] **P1 - Protect committed credentials (Q01)** - config.yaml; core/config.py; INSTALL.md.
  - The public repository must not receive working-tree passwords. Document the supported `AD_SERVER__HOST_PASSWORD` and `AD_SERVER__PLAYER_PASSWORD` environment overrides as the preferred operator path, consider a `config.example.yaml` template plus an ignored local config, and warn that `git commit -a` can publish credentials.
  - Acceptance: a configured checkout and built artifact contain no live passwords, documented environment overrides work, and the example configuration remains safe to commit.

- [ ] **P1 - Re-verify the working-tree WIP before committing (Q02)** - logic/dice.py; logic/llm/prompts.py; tests/test_chance_events.py.
  - Re-run the full Python suite and Node client tests after the conditional-occurrence changes recorded by QWEN-REVIEW.md; reconcile any stale `pytest-output.txt` failure before treating the WIP as complete.
  - Acceptance: the full offline test suite, client tests, Black and Flake8 pass, and the conditional-occurrence implementation and regression coverage land coherently.

- [ ] **P1 - Commit opening memory with accepted game state (R04)** - logic/llm_manager.py:\_request; logic/lobby.py:\_prepare_start.
  - Opening history is remembered before transcript.start and engine commit. A disk/filename failure returns to the lobby while retaining unpublished narrative; retry then consumes phantom facts. Stage or roll back resolver state with the game transition.
  - Acceptance: Injected transcript failure then Start retry retains exactly one accepted opening and the current party; End stays terminal; post-commit delivery failure never rolls back committed rounds.

- [ ] **P1 - Recover from failed mandatory and optional compaction (R05)** - logic/llm_manager.py:\_compact_if_needed; logic/llm/auditing.py:audit_summary.
  - Retries currently repeat the same summary strategy. Defer a rejected optional checkpoint when the original request fits; use bounded correction-aware repair/smaller prefixes for mandatory compaction and useful host failure diagnostics.
  - Acceptance: Audit rejection, expanding summaries and later-pass failures preserve facts and terminate predictably; no unnecessary pause for optional work and no FIFO loss.

- [ ] **P2 - Separate player attempts from trusted presence metadata (R06)** - logic/llm/prompts.py; logic/engine.py; logic/validation.py.
  - Line-oriented interpolation mixes user text with trusted SYSTEM annotations. Use separate structured actor/action/presence fields and control-safe identifiers/log rendering; keep chat outside context.
  - Acceptance: Forged actor lines/annotations remain untrusted data; valid multiline actions and exact names remain supported.

- [ ] **P2 - Preserve intended surprises in public output during play (R07)** - logic/llm/validation.py; logic/llm_manager.py.
  - Exact guidance fragments and English number patterns miss short/paraphrased/multilingual secrets and can collide with valid public rolls. Preserve unannounced surprises during play and add a measured output-disclosure corpus.
  - Acceptance: Cover short secrets, paraphrases and equal public/private values; record false positives/negatives and never claim model/regex checks guarantee secrecy.

- [ ] **P2 - Retain action drafts until server acceptance (R08)** - static/js/app.js:action submit handler; logic/engine.py:\_submit_action.
  - The input clears on send, before validation/acceptance. Keep pending text until echo/snapshot acknowledgment and add session/round-scoped idempotency if resending.
  - Product follow-up (S05): show accepted versus pending status and retain drafts through budget rejection or connection loss without allowing a forgotten saved identity to claim a character by public name alone.
  - Acceptance: Budget rejection and lost connection preserve text; retries cannot duplicate an accepted action or replay it next round.

- [ ] **P2 - Stop duplicate-tab takeover loops and reset session UI (R09)** - api/server.py; static/js/app.js:connectSocket, applySnapshot.
  - All closes reconnect and backoff resets before auth. Distinguish replacement/auth failure/transient disconnect, require explicit reclaim after replacement, and add session identity to reset log/round/control state.
  - Product follow-up (S05): tell a displaced tab that its character is active elsewhere, make reclaim explicit, and offer a local "forget this saved identity" action for shared devices.
  - Acceptance: Two tabs converge to one owner; bad credentials stop cycling; server restart and failed Start reconnect restore correct controls and round numbering.

- [ ] **P2 - Recover missed rounds and preserve access to full history (R10)** - static/js/app.js:applySnapshot, trimContainer; logic/lobby.py:\_snapshot_locked.
  - Existing DOM prevents snapshot state replacement after disconnect. Reload only receives current state; the 500-entry cap deletes early history without a retrieval path.
  - Add public event sequence/cursor replay and paginated/virtualized history. Acceptance: reconnect restores missed events once and users can reach the opening without unbounded DOM growth or private-memory exposure.
  - First step: track the last rendered completed round and include its full public result (outcomes and public dice) in snapshots. Use public events for in-session replay; HTML transcripts include the underlying guidance and rolls.
  - Product follow-up (S02): expose a searchable, paginated public journal and a deterministic player-safe export or "catch up since I left" view from the public event store. Add an optional narrative recap only after its cost and privacy are measured.
  - Review 2026-09-28: include session identity and monotonic event ordering; a latest narrative alone cannot recover missing player outcomes/public dice.

- [ ] **P2 - Bound slow-socket backpressure (R11)** - api/server.py:ConnectionManager.\_send_text, broadcast_global.
  - Broadcasts already serialize once; per-socket send locks and a five-second timeout close failing sockets (close timeout: two seconds). Broadcasts still await all sends.
  - Verify slow-client isolation and prompt player-disconnect handling under load; consider bounded delivery queues if needed. Acceptance: stalled receivers cannot hold up healthy clients or remain active turn participants indefinitely.
  - Review (2026-09-27): sends awaited under effects_lock can delay turn delivery and End by the five-second send plus two-second close timeouts. Use bounded ordered per-client queues with prompt presence updates on overflow/failure.
  - Review 2026-09-28: bound queue bytes and writer tasks; failure must promptly remove the current socket and update presence without marking a replacement disconnected.

- [ ] **P2 - Budget actual summary audits before spending inference (R12)** - logic/llm_manager.py:\_compact_if_needed; logic/llm/prompts.py:summary_audit_prompt.
  - Replace the fixed extra 512-token proxy with the actual audit framing/schema reserve and verify candidate size. Assess useful shrinkage with the upcoming formatted request, keeping conservative estimates labeled.
  - Acceptance: Near-limit summaries do not waste avoidable calls on unaffordable audits; all final requests remain checked and durable memory is retained on failure.

- [ ] **P2 - Reduce serial compaction-prefix probes and bound tokenizer overhead (R13)** - logic/llm_manager.py:\_compact_if_needed.
  - Growing-prefix scans repeatedly tokenize overlapping history, producing quadratic cumulative input volume when many prefixes fit.
  - Select likely prefixes using local estimates, then verify backend budgets and adjust conservatively. Preserve audit reserves, rollback and durable facts.
  - Acceptance: fewer backend probes for long histories with no over-budget summary/audit requests or lost memory.
  - Review 2026-09-28: add recoverable capability backoff, an aggregate preflight/tokenizer deadline and safe single-flight counting; check cache byte retention as well as entry limits. Never hide overflow or permanently cache failures.
  - Decide whether preflight tokenizer outages should use the same labelled conservative estimate as inference or remain a documented hard failure; align the behavior or explain the intentional divergence.

- [ ] **P2 - Validate compact retained records and preserve adjudication provenance (R14)** - logic/llm_manager.py:generate_resolution, \_request; logic/llm/prompts.py.
  - Review 2026-09-28: opening_memory_prompt and round_memory_prompt already avoid retaining full reusable instructions. The earlier 2,449-character request example is historical, not current behavior.
  - Retain compact actions, trusted presence, round IDs and relevant authoritative adjudication alongside outcomes; current action records omit dice/event provenance. Remove redundant current-state text only when already present in authoritative context.
  - Acceptance: measured request/retained-token reduction without privacy, injury, inventory or causal regressions. Implementation is present in part; quality/performance acceptance remains unverified.

- [ ] **P2 - Use a compact fact ledger plus recent rounds for narrative memory (R15)** - core/schemas.py; logic/engine.py; logic/llm_manager.py.
  - Maintain authoritative players, world, NPCs, resources and unresolved threads, applying validated changes from the existing resolution where feasible. Keep rich prose in transcripts/UI instead of retransmitting it indefinitely.
  - Separate immutable premise from changing facts; replace superseded facts and retrieve archived details when relevant. Freeze checkpoints between compactions when practical to preserve prefix reuse. Avoid adding mandatory summarization every round.
  - Acceptance: long-session comparison against current history uses fewer total tokens without regressions in identity, possessions, injuries, causal consistency or unresolved quests.
  - Experiment (2026-09-15): periodic checkpoints reduced tokens in a synthetic replay but lost a consumed item and changed a deadline. Kept disabled; a new summary audit rejected a faulty live summary and preserved history. A lossless compact ledger remains unfinished.
  - Review 2026-09-28: use versioned facts, source evidence and validated deltas; preserve current facts even when retrieval scores are low. Diagnose irreducibly oversized durable state rather than repeating failed summaries.

- [ ] **P2 - Reduce planner and audit cost without degrading adjudication (R16)** - logic/llm_manager.py:plan_dice, generate_resolution; core/schemas.py; config.yaml.
  - Planner prompts are centralized and the current RoundResolution schema already omits titles. Review 2026-09-28: current_state can duplicate the latest retained narrative; evaluate removing only provably redundant context.
  - Compare two-call behavior against safe deterministic handling of routine actions. A one-call experiment could supply server-generated candidate rolls and let the model select checks, but must evaluate selection bias; never let the LLM invent authoritative rolls.
  - Acceptance: compare total tokens, latency and coherent/fair outcomes. A routine-looking action must still account for contextual hazards.
  - Experiment (2026-09-15): shorter prompts did not consistently lower total cost and a candidate missed hidden-roll classification. Full planner remains default. Configured Gemma thinking is disabled to reserve bounded output for structured answers. Safe-action overrolling remains a live-model limitation; no one-call or deterministic bypass was adopted.
  - Review 2026-09-28: hidden-source classification currently adds one serial LLM call per hidden player. Evaluate a bounded per-player batch with sufficient facts, rather than assuming every round uses only two calls.

- [ ] **P2 - Share participant and presence projection with action preflight (R17)** - logic/engine.py:\_submit_action, \_launch_round_locked, \_resolve_round_work.
  - Preflight includes persistently absent players excluded from actual resolution, and omits later presence annotations. Reuse an immutable projection and revalidate relevant presence changes; account for later dice/event context.
  - Acceptance: No false rejection from permanently absent participants or unexpected metadata overflow; preserve join order and simultaneous actions.

- [ ] **P2 - Reject malformed auth fields cleanly and clarify retry accounting (R18)** - logic/lobby.py:\_authenticate; core/schemas.py; logic/llm_manager.py; logic/usage.py.
  - Non-ASCII reconnect_token reaches string compare_digest and raises TypeError. Bound/validate fields with strict event schemas; distinguish semantic failures and whole-round retries from provider retry counters.
  - Acceptance: Malformed fields fail without tracebacks/state changes; all retry layers and semantic rejection are identifiable while unreported tokens stay unknown.

- [ ] **P2 - Keep operator secrets out of built distributions (R19)** - pyproject.toml:wheel.force-include; core/config.py.
  - The wheel includes live config.yaml. Package sanitized defaults/examples and load operator config externally; add future artifact-content verification without exposing credentials.
  - Acceptance: Building from a configured checkout cannot package passwords/API keys; editable/wheel launches use the intended external settings.

- [ ] **P2 - Verify dependency bounds and clean installation (R19)** - INSTALL.md; pyproject.toml; api/tls_bootstrap.py.
  - Runtime documentation now covers HTTPS, certificate lifetime and address coverage, private transcript contents, reconnects, and context counting. Minimum-version compatibility still needs verification.
  - Verify minimum dependencies support APIs used, including beta.chat.completions.parse; constrain a tested set. Acceptance: clean installation/documented startup works and counting limitations are explicit.
  - Review 2026-09-28: validate both editable and wheel installations and external configuration discovery. No dependency advisory scan or installation was performed in this review.

- [ ] **P2 - Bound transcript filenames and make creation/retry resilient (R20)** - logic/transcript.py:start, \_write.
  - Promoted from Someday: title-derived filenames are unbounded and exists-then-append is not exclusive creation. Reserve bounded filenames in offloaded I/O, define partial-write handling and avoid phantom opening memory (R04).
  - Bound `ScenarioTitle.title` (for example, 80 characters) and defensively truncate the sanitized filename. Keep post-finalize appends from masking a committed round: handle the expected `RuntimeError` explicitly, log the race, and preserve the state/broadcast outcome.
  - Acceptance: Long titles, concurrent reservations and injected disk errors preserve accepted state; HTML transcripts stay escaped and are not automatically served by the game. Existing serialized writes/stable colors and removal of unused previous_state are retained.

- [ ] **P2 - Validate TLS artifacts and robust offline startup (R20)** - api/tls_bootstrap.py.
  - Handle corrupt/mismatched cert/key pairs and interrupted renewal; provide explicit address/certificate configuration and true offline fallback. Review leaf ca=False and least-privilege key permissions without silently altering trust stores.
  - Acceptance: Malformed/expired/mismatched files and no-network startup produce controlled recovery or actionable errors; renewal never silently reuses a mismatched pair.

- [ ] **P3 - Simplify the resolver boundary and outcome presentation (R21)** - logic/models.py; logic/engine.py; logic/llm_manager.py.
  - Make resolver fakes implement the required protocol, remove optional-method compatibility paths, and retain the already-extracted logic/llm/errors.py exceptions (confirmed in the 2026-09-28 static review).
  - Remove unreachable UUID/missing-outcome fallbacks after exact-name validation; establish one normalization boundary while keeping remembered and displayed outcomes consistent.
  - Acceptance: no unchecked resolution fallback, identical valid outcomes, and existing fake-resolver/lifecycle coverage passes.

- [ ] **P3 - Batch frontend log rendering (R21)** - static/js/app.js.
  - Append round content as a batch and scroll once per event instead of reading layout after every insertion.
  - Acceptance: action deduplication, DOM limits, round styling and reconnect rendering remain correct; long rounds avoid repeated forced layouts.
  - Preserve a reader's scroll position unless they are following the latest entry; evaluate modal focus/restore and live-region volume.

- [ ] **P3 - Reduce the favicon payload (Q05)** - static/favicon/favicon.svg.
  - The SVG is dominated by embedded raster data and is much larger than the existing PNG favicon assets. Strip embedded rasters or remove the oversized SVG from favicon priority.
  - Acceptance: the selected favicon remains visually correct at supported sizes and the shipped asset is materially smaller without changing unrelated branding.

- [ ] **P3 - Preserve action focus after chat echoes (Q06)** - static/js/app.js:handleChatEcho.
  - Do not unconditionally focus the chat input when an echo arrives; restore focus only when chat already had focus so incoming chat cannot steal focus from an action being composed.
  - Acceptance: chat submission still works, action-input focus is preserved, and keyboard navigation/reconnect behavior remains unchanged.

- [ ] **P3 - Document provider and tokenizer configuration semantics (Q07)** - INSTALL.md.
  - Document that `tokenizer_encoding` is not used for compatible-provider counting when backend tokenization is available, and keep the documented environment overrides aligned with the actual `AD_*` settings behavior.
  - Acceptance: the installation guide accurately describes both provider paths and does not imply that a local encoding setting overrides backend tokenizer results.

## Waiting On

- [ ] **Measure representative session length and player idle time** - Backend profile received and live runs completed on 2026-09-15: llama.cpp b10964/b29c606e2, Gemma 4 26B A4B Q4_K_XXL, canonical template, one 128000-token slot and q8_0 KV. A synthetic OpenAI run on 2026-09-27 completed 16 rounds without added inter-round waits; it stopped at round 17 after three dice-planning failures and repeated compaction rollbacks. Typical human delay/session length and idle-time effects remain unmeasured.

## Someday

- [ ] **P2 - Durable save/resume (S04)** - logic/lobby.py; logic/engine.py; logic/llm_manager.py; logic/transcript.py.
  - Save versioned game state, roster, accepted actions, pending authoritative dice/events, resolver memory and public event cursors atomically. Protect reconnect credentials and private guidance on disk, and keep HTML transcripts as archives rather than save files.
  - Resume only from a clearly defined committed checkpoint; never reroll a pending round or replay a committed action. Define recovery validation and migrations before combining this with multiple sessions or host reset.
  - Acceptance: injected interruption leaves either the previous checkpoint or one complete newer checkpoint, credentials/private data remain protected, and migrations reject incompatible state without reviving an ended game.

- [ ] **P3 - Host memory inspector and correction history (S03)** - future fact-ledger UI; core/schemas.py; logic/llm_manager.py.
  - Show the host durable world/player facts, source rounds and unresolved threads, with explicit corrections recorded as versioned, auditable changes. Keep private and public facts distinct during play, preserving unannounced guidance until its effects unfold.
  - Depends on R15's validated fact ledger; arbitrary prose edits must not bypass adjudication or resurrect consumed items and later consequences.

- [ ] **P3 - Ready, pause and deliberate idle controls (S06)** - logic/lobby.py; logic/engine.py; static/js/app.js.
  - Let the host see party readiness before Start, pause between rounds, and resolve an inactive connected player through an explicit idle decision rather than silently inventing actions. Preserve join order and existing departure/return annotations.
  - Define whether a pending action survives a pause and never mutate accepted actions after authoritative dice are generated.

- [ ] **P3 - Readability and accessibility preferences (S08)** - static/js/app.js; static/css/style.css; templates/index.html.
  - Offer font-size, contrast, reduced-motion, keyboard-focus and "follow latest" controls. Preserve reader scroll position and use batched polite announcements instead of announcing every appended element.
  - Validate narrow-screen and keyboard-only use without changing the documented desktop/mobile layout as an incidental implementation detail.

- [ ] **P3 - Scenario presets with public/private separation (S09)** - logic/lobby.py; core/config.py; static/js/app.js.
  - Save reusable host settings, public premise and private guidance as distinct versioned fields. Preview the public portion when sharing an unrevealed scenario; presets must not contain passwords, reconnect tokens or prior players' private state.
  - Include an explicit scenario language choice rather than relying on backend-dependent language inference.

- [ ] **P3 - Compare optional CPU advisors in shadow mode (VON / Needle)** - logic/models.py; future isolated evaluation only.
  - VON: bounded classification, particularly cited hidden-source audits. Needle: evidence-linked fact extraction and semantic journal search. Compare each with deterministic Python and deltas from the existing resolution before adding a model.
  - Pin code/weights/native artifacts; VON requires a separate Python >=3.12 runtime if preserving Anyworld's >=3.11 floor. For Needle disable telemetry, reset independent requests, disable automatic real-date injection for game facts, and avoid automatic tool execution.
  - Acceptance: held-out human labels, actual CPU/RAM/p95 latency, privacy/omission errors, abstention and total fallback/repair cost justify one opt-in use. No live inference, downloads, installation or training was performed in this review. See ASTRA-REVIEW.md and SUGGESTIONS.md.
  - Use one replaceable optional-advisor boundary with timeout, abstention and fallback. Compare deterministic Python, the current LLM, VON and Needle on the same labeled cases, start with one candidate backend, and never expose private advisor rationale or make either advisor authoritative.

- [ ] **P3 - Revisit OpenAI cache controls if deployed usage justifies tuning** - core/config.py; logic/llm_manager.py.
  - Keep reusable prefixes stable. Use routing/retention options only when supported by the selected model and Chat Completions endpoint; never pad prompts or transfer llama.cpp flags to OpenAI. Cached input still consumes context. [OpenAI prompt caching guidance](https://developers.openai.com/api/docs/guides/prompt-caching).
  - Measurement (2026-09-27): synthetic two-player run on GPT-5.6 Luna using default caching completed 16 rounds with no added idle waits. Across setup and the failed 17th round: 250,560 input tokens, 117,808 cached reads (47%), 131,154 cache-write tokens and 7,900 output tokens; estimated cost $0.0449 versus $0.0596 without cache reads/writes (about 25% lower). Resolution-call cache reads rose to about 95% by round 16; dice-planning calls had no cache hits. Round 17 stopped after three dice-planning failures, with context compaction rolling back each time.
  - Priority conclusion: demoted from P2 to P3. Default caching already yields substantial reuse and estimated savings, so no immediate cache-control change is justified. Revisit if real player idle intervals, cold/expired-cache behavior, or production cost data show a gap. The round-17 compaction failure is a separate memory/reliability concern, not evidence that cache controls need tuning.

- [ ] **P3 - Support multiple sessions** - Isolate engines, resolvers, credentials, transcripts and cancellation before adding concurrent sessions/workers. Host reset after ENDED is implemented; concurrent sessions remain open.
- [ ] **P3 - Version static assets reproducibly** - Replace manual ?v= values with content/build hashes and suitable cache headers so unchanged assets stay cached and edits invalidate reliably.

## Done

- [x] **Print a connected Quick Tunnel banner through normal Compose startup** (2026-10-07)
  - Replaced host launch scripts with an owned background task in the game's lifespan.
    It reads cloudflared's private readiness/hostname API and announces only a connected,
    validated Quick Tunnel address. Shutdown cancels pending discovery; missing tunnels
    do not block startup. The metrics port is not published.
  - Display Uvicorn's internal `uvicorn.error` logger as `uvicorn.server`, preserving
    severity, colors and the original record for other log handlers.
  - Validation: 423 offline tests, four disposable container variants, and a live
    Quick Tunnel URL announcement passed; Black, Flake8 and `git diff --check` passed.

- [x] ~~Separate OpenAI tokenizer selection from local inference~~ (2026-10-07)
  - Added `openai_tokenizer_encoding`; examples use `auto` for known tiktoken mappings.
    Local llama.cpp retains native template/tokenizer counting. Omitted/null preserves
    the legacy setting; unknown models and mismatches retain conservative estimates.

- [x] ~~2026-10-07 — Review and clarify project documentation~~ Added clear player/host
      setup paths, configuration-copy steps, Docker start/stop and backup instructions, and
      troubleshooting. Corrected removed chance-text instructions, new-game behavior, certificate
      coverage, output defaults and debug-file formats. Maintenance notes distinguish current
      contracts from historical reviews. Checked local links, heading anchors and YAML examples.

- [x] ~~2026-10-04 — Structured chance-rule form only~~ Removed the legacy chance-text input,
      payload field, validation and transcript fallback. Renamed the section to
      “Chance-based event rule” and added simple instructions; updated saved-scenario fields and asset cache versions.

- [x] ~~Add browser-local scenario save, load and delete controls~~ (2026-10-04)
  - Named localStorage saves preserve the written scenario, including all guidance and other information.
    Overwriting and deletion require confirmation; storage failures report an error without
    overwriting existing saves. Scenario saves stay completely local

- [x] ~~P2 - Keep public History in memory with manual JSONL export~~ (2026-10-04)
  - Removed automatic `.public_games/` writes and disk archive recovery. Full public event
    history remains in memory for the current game, with replay, pagination, search and browser
    JSONL export preserved. Starting a new game or restarting the server clears that history.
  - Regression coverage checks no filesystem access, more than 3,500 retained events, privacy,
    response-size limits, retry/cancellation ordering, and export requests after End.

- [x] ~~P3 - Structure private guidance in HTML transcripts~~ (2026-10-04)
  - Added Freeform guidance and Chance event subtitles. Structured events show labeled chance,
    timing, scope, optional trigger, eligibility and effect with readable config values.
  - Legacy chance rules keep their original text under their own subtitle. Guidance and chance
    text remain escaped and host-side during play; the combined resolver context is unchanged.
  - Validation for both changes above: 386 Python tests and 22 Node client tests passed;
    Black, Flake8 and diff whitespace checks passed. No live inference was run.

- [x] ~~P2 - Clean up Start new game after ENDED~~ (2026-10-04)
  - The host retains its authenticated identity and returns to scenario creation with a fresh
    engine, resolver and public journal. Previous players disconnect and must explicitly rejoin;
    the old resolver closes and a new, collision-safe HTML transcript is created on Start.
  - Suppressed late preflight errors after a handler loses engine/socket ownership. Saved drafts
    now include session IDs, preserving same-game reloads while clearing old or unscoped drafts.
    Session changes reset History filters/cursors, export state and player colors.
  - Regression coverage checks late errors after player rejoining, draft reloads, History reset,
    and distinct same-title HTML files that exclude old scenario/private guidance and leave the
    finalized previous file unchanged.
  - Validation: 388 Python tests and 22 Node client tests passed; Black and Flake8 passed.
    Python reported the existing Starlette deprecation warning. No live inference was run.

- [x] ~~P2 - Add an option to start a new game once last one is finished~~ (2026-10-04)

- [x] ~~P3 - Evaluate multilingual play~~ (2026-10-04)
  - User-reported manual evaluation across several game playthroughs; considered complete.
    OpenAI narration follows the scenario's language, while compatible-provider prompts
    currently request English. These existing provider instructions remain unchanged.

- [x] ~~P2 - Remove the private chance-rule JSON preview~~ (2026-10-04)
  - Removed the host-form preview, its input listener, DOM reference and CSS. Structured chance
    rules still validate on submission and report errors through the existing scenario form.
  - Refreshed stylesheet and script cache versions and updated the current UI maintenance guide.
  - Validation: 389 Python tests and 24 Node client tests passed; Black and Flake8 passed.
    Python reported the existing Starlette deprecation warning.

- [x] ~~Record historical local backend benchmark observations~~ (2026-10-04)
  - Added BENCHMARKS.md with the existing 2026-09-28 adjudication, memory, tokenizer and round
    usage observations, including the hidden-hazard false negative and measurement limits.
  - The historical local runners and raw reports are absent from this checkout. No live
    inference or benchmarks were rerun for this documentation and commit.

- [x] ~~P1 - Restore browser WebSocket routing and refresh cached scripts~~ (2026-10-03)
  - Corrected the accidental `clientSession.ws` substitutions in the protocol and URL path:
    HTTP uses `ws`, HTTPS uses `wss`, and both connect to `/ws/{client_id}`. The old path
    caused 403 handshake rejections. Bumped script URLs from `recovery-1` to `recovery-2`.
  - Added the expected HTTPS socket URL to the existing reconnect regression test.

- [x] ~~P2 - Make scenario creation fit the viewport and pair labels with fields~~ (2026-10-03)
  - Widened the host card from 36rem to a viewport-bounded 48rem. Grouped chance labels above
    their own controls in two columns, stacking at <=700px; controls shrink and previews wrap.
  - Bumped stylesheet version from 14 to 15. Backend asset-serving and client tests passed;
    no rendered desktop/mobile browser layout verification was performed.

- [x] ~~P2 - Clarify supported password environment overrides~~ (2026-10-03)
  - Launch validation now mentions `AD_SERVER__HOST_PASSWORD` and
    `AD_SERVER__PLAYER_PASSWORD` alongside YAML. Regression coverage verifies both override
    YAML values. README.md explains `.env`/process precedence and the manual config template.

- [x] ~~P1 - Ignore inherited OpenAI organization/project settings on game clients~~ (2026-10-03)
  - SDK clients clear organization/project after construction, preventing unrelated
    `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID` from causing 401 responses with the game's key.
    The process environment remains intact and the configured API key is unchanged.
  - Offline request-header regressions cover both direct OpenAI and compatible providers.
    Before implementation, a user-authorized direct title request using the game call path
    succeeded with `gpt-5.6-luna`; the user confirmed clearing shell organization/project
    variables resolved the game failure. No live post-fix inference or benchmark was run.
  - Final code validation: 338 Python tests and 21 Node client tests passed; Black and Flake8
    passed. The Python suite reported the existing Starlette/httpx deprecation warning.

- [x] ~~P2 - Use only `AD_OPENAI_API_KEY` for direct OpenAI credentials (Q03)~~ (2026-09-29) - core/config.py; INSTALL.md.
  - Direct OpenAI configuration now ignores the legacy `OPENAI_API_KEY` name, YAML `api_key`, and
    `AD_LLM__API_KEY`; startup fails clearly when `AD_OPENAI_API_KEY` is missing.
  - Compatible providers retain their existing generic `api_key` configuration. Regression tests
    cover the accepted variable and rejected fallbacks.

- [x] ~~P3 - Remove the dead auth payload-handler entry (Q04)~~ (2026-09-28) - logic/engine.py; tests/test_engine.py; tests/test_priority_one_lifecycle.py.
  - Removed authentication from `GameEngine.PAYLOAD_HANDLERS`; the socket gateway continues to call the dedicated lobby authentication method, while game payload routing contains only post-auth events.
  - Updated engine tests to use the same dedicated authentication boundary. Focused engine, lifecycle and transport tests: 54 passed; Black and Flake8 passed.

- [x] ~~P2 - Apply the 2026-09-27 LLM context-manager review~~ (2026-09-28) - logic/llm_manager.py; core/schemas.py.
  - Removed the redundant per-attempt timeout, centralized private-guidance filtering, and
    replaced full-message JSON cache-key serialization with a structural key.
  - Repairs no longer restart the full transient retry sequence, avoiding quadratic provider
    calls. Hidden-check and chance-outcome validation now use a dedicated `AuditVerdict` schema;
    summary compaction retains `SummaryAudit`.
  - The participant-schema cache sizing, startup setting validation and opt-in private debug
    logging were reviewed and left unchanged because their existing bounds/documentation are
    sufficient. Proactive rate limiting was not added without a configured provider quota.
  - Validation: focused context, chance-event, retry, usage and schema tests passed; Black and
    Flake8 passed. No live inference or benchmark was run.

- [x] ~~P2 - Measure aggregate action prompts during preflight~~ (2026-09-28) - logic/llm_manager.py.
  - Preflight now reuses the live dice-planning and baseline resolution prompt builders and their participant schemas. The brittle `1,024 + 256 * action_count` allowance was removed, and shared resolution instructions are counted before they are sent.
  - Resolution requests are checked again after generated dice and chance-event context are available. Validation: all 271 Python tests passed; Black and Flake8 passed; no live inference or backend benchmark was run.

- [x] ~~P3 - Keep transcript colors stable across missing players and sorted dice~~ (2026-09-27) - logic/transcript.py.
  - Actions, outcomes, public dice and private checks now use the supplied join-index mapping, with the same eight-color palette as the UI. Positional CSS no longer changes identities when participants are omitted; names remain HTML-escaped.
  - Validation: regression coverage includes reordered outcomes, absent players, sorted dice, palette wrapping and escaped names. Full suite: 233 Python tests and 16 frontend tests passed; Black/flake8 passed. No live inference or backend benchmark was run.

- [x] ~~P2 - Reuse message tokenization across output schemas~~ (2026-09-27) - logic/llm_manager.py.
  - Cache formatted-message counts independently of response schemas, then add each schema's allowance. Schema serialization uses a bounded cache. Retained-context measurements reuse base counts without inheriting schema allowances.
  - Validation: 83 token-cache, budget, semantic and usage tests passed; distinct dice and resolution preflight prompts are each tokenized once while repeated schemas reuse the cached message count. Model/template changes recount and backend failures remain retryable. Black/flake8 passed; no live latency or token-savings claim.

- [x] ~~P2 - Preserve player names during OpenAI schema cleanup~~ (2026-09-27) - logic/llm_manager.py:participant_schema.
  - Schema cleanup preserves property and definition names while removing unsupported constraints from their schemas. Keyword-like player names remain required and available in dice, resolution and memory schemas.
  - Validation: 107 semantic/schema and chance-event tests passed, including keyword-name regressions; Black/flake8 passed.

- [x] ~~P1 - Preserve committed rounds when delivery or usage reporting times out~~ (2026-09-27) - logic/engine.py; logic/lobby.py.
  - Token measurement runs outside the effects lock and inference deadline with a five-second limit; failures use the labelled snapshot estimate. Usage is published once after final accounting and turn delivery.
  - Post-commit delivery failures preserve committed state and resume the turn directive instead of creating a paused round with no actions to retry. End cancels blocked telemetry.
  - Validation: 47 lifecycle, usage and engine tests passed, including new delayed/failed telemetry, End-during-refresh and post-commit deadline regressions; Black/flake8 passed.

- [x] ~~Normalize unsupported hidden-roll labels to public checks~~ (2026-09-27)
  - Missing, percentage-based, or unrelated private sources no longer fail dice planning. The
    action's planned d100 remains and is treated as public; valid cited secret sources stay hidden.

- [x] ~~Retry failed rounds automatically before pausing~~ (2026-09-27)
  - Up to two round-level retries reuse pending actions and dice, keeping the thinking indicator
    active without intermediate error messages. The existing overall deadline, cancellation and
    stale-generation guards remain effective. Exhaustion preserves manual Retry/End recovery.

- [x] ~~Narrow conditional audits and clarify unspecified chance triggers~~ (2026-09-26)
  - Conditional audits return structured occurrence differences instead of unrestricted critiques
    of public rolls or missing random results. Model-classified per-round rules bypass this audit.
    The planner defaults unspecified triggers to per-round and preserves action rolls during
    privacy repairs. Trigger interpretation still depends on model accuracy without explicit cadence.

- [x] ~~Prevent planning audits from demanding unrolled chance results~~ (2026-09-26)
  - Per-round-only public plans bypass the planning audit. Remaining audits check conditional
    occurrences and hidden-check classification before rolls, excluding per-round event data.
    Source choices are constrained to non-percentage guidance lines; repairs retain the rejected
    plan as context. Offline validation: 212 Python tests passed; live behavior remains unverified.

- [x] ~~Separate the single percentage event from freeform DM guidance~~ (2026-09-26)
  - Scenario setup now has an optional single-line `chance_event` field accepting exactly one
    whole-number percentage rule per game. Percentage rules in freeform guidance are rejected;
    the remaining guidance is non-probabilistic steering. README.md, INSTALL.md, AGENTS.md and
    the host-form documentation describe the same contract. Per-round checks are generated by
    Python, conditional triggers remain model-classified, and setting-conflicting attempts can
    receive public low-plausibility checks instead of being rejected as impossible.

- [x] ~~Separate title preparation from the generated opening~~ (2026-09-17)
  - Scenario submission generates only a title. Joining players see the host-typed prompt; Start Game generates the opening with all joined names. The prompt requests setting, goal, roles, paragraphs, and consistent physical consequences. These instructions do not guarantee model coherence.
- [x] ~~Update transcript appearance and distinguish scenario versions~~ (2026-09-17)
  - Transcripts match the game's green palette and retain both Original scenario prompt and Opening scenario. Hidden guidance and checks are archived for the host rather than broadcast during play; the host can share the transcript after the session.
- [x] ~~Correct retained-context display and add operational logging~~ (2026-09-17)
  - Retained context uses backend tokenization when available, with labelled fallback estimates and limit source. Logs cover compaction, inference lifecycle, retries, and private-guidance dice checks.
- [x] ~~Clean up test artifacts and refresh the documentation~~ (2026-09-17)
  - Per-test temporary directories are deleted on teardown, including after failures. Read-only reviews may run offline tests when temporary files are allowed. README is simplified; INSTALL.md holds setup and diagnostics. Latest code validation: 157 Python and 15 JavaScript tests passed; formatting/lint checks passed, with an existing Starlette/httpx deprecation warning.

- [x] ~~P2 - Instrument total round cost and cache reuse before tuning~~ (2026-09-15)
  - Per-attempt, round and game totals now include summaries/audits, retries, failures, cancellations, unknown counters and work time. The expandable usage panel separates retained context from consumption. Final deployed llama.cpp matrix: 8/8 successful cases, 2/6 players, short/long actions, cold first calls and warm repeats after 20 seconds of artificial idle. Compaction accounting is also covered offline and by a live summary/audit pair.

- [x] ~~P2 - Benchmark llama.cpp cache interference and stable prefixes~~ (2026-09-15)
  - Measured processed/reused prompt counters and latency on the deployed one-slot server, including explicit slot 0. No routing change was justified; separate auxiliary capacity remains unmeasured. Preserve original validated assistant text when normalization makes no change, avoiding unnecessary token-prefix changes. Cold first calls use cache_prompt=false because cache erase returned HTTP 501.

- [x] ~~P2 - Cache message token counts and tune compaction cadence~~ (2026-09-15)
  - Added bounded content/tokenizer and request/template/schema count caches; failed tokenizer fallbacks remain retryable. Necessary compaction targets a lower watermark with transactional validation and audit. Optional periodic checkpoints remain disabled after live retention failures. Repeated live request counts agreed (1354 tokens); counting took 4.23 ms initially and 0.31 ms cached. No general percentage saving is claimed.

- [x] ~~P2 - Validate semantics before remembering or committing LLM output~~ (2026-09-15)
  - Exact participant schemas and semantic checks reject missing/extra names, empty required content and invalid hidden-roll membership before memory/state commit. Bounded repair retains authoritative actions and dice; failed rounds pause. Normalize displayed/remembered outcomes consistently. Summary auditing rejects detected durable-fact loss transactionally; model-assisted validation cannot prove arbitrary semantic correctness.

- [x] ~~P2 - Fix stale tests and cover context/cache/lifecycle behavior~~ (2026-09-15)
  - At that milestone, 88 offline tests passed, with fake clients and isolated settings/transcripts. Added semantic, accounting, cache, template-option and discovery-recovery regressions plus separate opt-in live runners. Black, Flake8 and JavaScript syntax checks pass. Corrected a stale dice-description assertion without changing the dice distribution. One existing Starlette/httpx deprecation warning remains.

The older completed items below preserve the original problem statements; their Implementation
lines describe the fixes. Historical P1 implementation was validated with fake-model and ASGI regression tests.
The 2026-09-15 P2 work adds deployed llama.cpp measurements above; these do not establish
general long-session coherence or OpenAI performance.

- [x] ~~P1 - Enforce complete token budgets on every call~~ (2026-09-14) - logic/llm_manager.py:\_request, \_compact_if_needed, \_bounded_messages, \_count_tokens; core/config.py.
  - The reserved 2,048 tokens (or quarter-context) are never passed as an output cap. Schema/chat-template overhead is absent, cl100k_base can mismatch the local model, and len/4 can underestimate input. Summary requests bypass bounding.
  - Add provider-supported generation limits per request type, model-appropriate counting and a safety margin. Preflight aggregate actions/scenario before accepting an impossible round. Handle truncated output explicitly.
  - Acceptance: input + output allowance fits effective context for dice, opening, rounds and summaries; cover large parties, guidance and Unicode. Label fallback counts as estimates.
  - Implementation: Implemented per-kind output caps, complete request/schema/margin admission, backend template/tokenizer counting, conservative fallback, aggregate action preflight and truncation/deadline handling.

- [x] ~~P1 - Protect durable memory from FIFO eviction~~ (2026-09-14) - logic/llm_manager.py:\_compact_if_needed, \_bounded_messages; core/schemas.py:ContextSummary.
  - Compaction ignores the upcoming prompt size. Large actions can force unsummarized deletion; the historical summary itself becomes the oldest disposable pair. Failed compaction silently falls back to forgetting.
  - Separate durable memory from recent dialogue, compact against the complete upcoming request, validate bounded summaries and require useful size reduction. Retain old memory until success; pause/reject instead of dropping essential facts.
  - Acceptance: inventory, injuries, locations, NPC relationships and unresolved promises survive repeated compaction, long turns and summary failures without resurrecting obsolete facts.
  - Implementation: Replaced FIFO eviction with separate durable memory and transactional, budgeted merging; failure, cancellation, empty or expanding summaries preserve original memory.

- [x] ~~P1 - Supply authoritative facts and private triggers to dice planning~~ (2026-09-14) - logic/llm_manager.py:plan_dice; logic/engine.py:\_resolve_round; config.yaml.
  - Planning asks which rolls originate from private guidance but excludes that guidance. It only sees the latest paragraph, while the system prompt discourages repeating unchanged state; prior obstacles/capabilities can disappear.
  - Give the planner compact relevant facts/rules and private triggers, distinct from public narrative. Test that generated public prose preserves unannounced checks during play.
  - Acceptance: persistent injuries, locked doors and hidden hazards affect planning across quiet rounds and compaction; ordinary rolls remain public and private rolls remain private.
  - Implementation: Planner now receives genesis/private rules, durable memory and recent facts. Hidden checks stay out of public dice broadcasts (current server-side transcripts include them), and explicit output disclosures are rejected before history commit.

- [x] ~~P1 - Authenticate sockets before subscriptions or replacement~~ (2026-09-14) - api/server.py:connect, broadcast_global, websocket_endpoint; logic/lobby.py.
  - All accepted UUID sockets receive game/chat broadcasts before authentication. Browser filtering is not authorization. Reusing a UUID closes the old socket before credentials are checked; engine handlers identify callers by UUID rather than authenticated socket ownership.
  - Separate pending/authenticated connections, bind actions to authenticated sockets, and replace sessions only after successful authentication. Validate passwords in ASGI lifespan as well as CLI; bound pending connections/auth attempts.
  - Acceptance: raw unauthenticated sockets cannot receive game data, displace players or act under existing IDs; cover duplicate UUIDs and direct ASGI startup.
  - Implementation: Pending sockets are isolated; successful authentication atomically promotes sockets. Reconnects require private per-player tokens, commands check socket ownership, and lifespan enforces credentials/admission limits.

- [x] ~~P1 - Make inference independent of the receive loop and keep ENDED terminal~~ (2026-09-14) - api/server.py:websocket_endpoint; logic/engine.py:\_resolve_round; logic/lobby.py setup/start/end.
  - Inline inference blocks the triggering player's chat and host controls. Another socket can end play, but setup/start success and round-error paths overwrite ENDED. Success checking and committing are separate, and finalization can race transcript writes/events.
  - Use owned inference tasks, session/round generation identifiers, cancellation/deadlines and bounded retries. Check validity atomically with commit; serialize transcript writes/finalization and close tasks/clients in lifespan.
  - Acceptance: end during every inference phase/error never revives play or writes after finalization; all connected players retain responsive chat.
  - Implementation: Inference uses owned tasks and generation guards; end/commit events and transcript writes are ordered. Errors pause with actions/dice retained and a host retry control. Shutdown cancels work and closes clients.

- [x] ~~P1 - Resume turns after everyone disconnects~~ (2026-09-14) - logic/engine.py:\_next_turn_locked; logic/lobby.py:\_authenticate.
  - If inference finishes with nobody connected, active_player_id becomes None. Reauthentication never invokes turn selection, leaving play stuck. Older resolution commits can also clear newer departure/return flags.
  - Resume under the lock, broadcast the directive, and version connection transitions. Acceptance: reconnect after total disconnect during input/inference resumes exactly one correct turn and narrates transitions once.
  - Implementation: Reauthentication resumes stalled turns; new rounds reset to join order. Versioned connection transitions are not cleared by stale outcomes.

- [x] ~~Review working-tree code and update AGENTS.md with current architecture and known discrepancies.~~ (2026-09-14)
- [x] ~~Create prioritized token, coherence, cache and reliability recommendations.~~ (2026-09-14)
- [x] ~~Make token usage visible to all players.~~ (2026-09-01)
- [x] ~~Game transcripts should use the same player colors as the player round actions pane, to give it a more lively look.~~ (2026-09-01)
- [x] ~~Add the game name in front of the scenario name: Anyworld - (scenario name)~~ (2026-09-01)
- [x] ~~Add more logging.~~ (2026-09-01)
  - ~~logic/llm_manager.py logs set_genesis, generate_initial_state, generate_start_state, plan_dice, generate_resolution, context compaction, and backend token statistics.~~
  - ~~api/tls_bootstrap.py logs when certificates need to be renewed.~~
- [x] ~~class \_NonSuccessOnly(logging.Filter) in app.py does not work. All "200 OK" events are logged to the console.~~ (2026-09-01)
- [x] ~~After the host has entered the scenario, the AI fails to generate a descriptive name for the session, instead telling the host that “Untitled Session” is ready. Only after the game is started is the name generated.~~ (2026-09-01)
- [x] ~~Restrict dice rolls in system prompt to truly difficult/risky/absurd attempts only~~ (2026-08-28)
- [x] ~~Use brighter player colors for the current turn and dim past rounds~~ (2026-08-28)
- [x] ~~Fix "weaving the outcome" loading text scroll jump~~ (2026-08-28)
- [x] ~~Convert logged games to readable HTML~~ (2026-08-28)
- [x] ~~Investigate Safari login page issue~~ (2026-08-28)
- [x] ~~Show token usage approximation to host~~ (2026-08-28)
- [x] ~~Complete the test suite~~ (2026-08-28)
- [x] ~~Validate client_id format on the server~~ (2026-08-28)
- [x] ~~Hash passwords in transit~~ (2026-08-28)
- [x] ~~Cap DOM growth in log and state panes~~ (2026-08-28)
- [x] ~~Reconnect the WebSocket instead of reloading the page~~ (2026-08-28)
- [x] ~~Enforce input length limits client-side~~ (2026-08-28)
- [x] ~~Add a host end-game control~~ (2026-08-28)
- [x] ~~Dim previous-round entries for readability~~ (2026-08-28)
- [x] ~~Cache system prompt token count~~ (2026-08-28)
- [x] ~~Deduplicate prompt sources~~ (2026-08-28)
- [x] ~~Hoist payload handler map~~ (2026-08-28)
- [x] ~~Move dm-thinking indicator out of log pane~~ (2026-08-28)
- [x] ~~Compact frontend state growth~~ (2026-08-28)
- [x] ~~Add DM dice-roll function~~ (2026-08-28)
- [x] ~~Drop redundant roster broadcast after auth~~ (2026-08-28)
