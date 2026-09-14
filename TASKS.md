# Tasks

Static review: 2026-09-14, including pre-existing working-tree edits. Implementation was read
only; venv and .venv were excluded. No tests, app startup or live inference were performed.
P1 = correctness/security or substantial waste; P2 = optimization/reliability; P3 = optional.
Active items are recommendations, not implementation already authorized or underway.

## Active

- [ ] **P2 - Instrument total round cost and cache reuse before tuning** - logic/llm_manager.py:\_request, \_compact_if_needed; engine token events; frontend token chart.
  - last_token_usage represents only the last request; summary usage is absent. It cannot represent total round consumption, cumulative game cost or retained context.
  - Record request kind, actual/estimated input, completion, available cache counters, latency, errors and retries without logging private prompts. Separate retained-context occupancy from cumulative usage; missing counters mean unknown.
  - Acceptance: a compacting round accounts for dice, summary and resolution. Baselines cover 2/6 players, short/long actions, cold/warm cache and realistic human delays.

- [ ] **P2 - Benchmark llama.cpp cache interference and stable prefixes** - logic/llm_manager.py:\_request, plan_dice, \_compact_if_needed.
  - Short dice, long narrative and summary calls alternate on the same model. They may displace reusable context depending on deployed slots/cache settings; this is an unmeasured hypothesis.
  - Compare default routing with supported slot affinity or separate auxiliary capacity. A separate HTTP client alone does not isolate KV cache. Verify cache_prompt behavior on the deployed build and account for slot memory/context tradeoffs.
  - Keep system/genesis/schema serialization stable until deliberate compaction. Check whether model_dump_json reserialization changes the token prefix compared with original validated assistant text.
  - Acceptance: report processed/reused prompt tokens and round latency with unchanged coherence before choosing settings. [llama.cpp server reference](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md).

- [ ] **P2 - Tune OpenAI caching only for supported model/API capabilities** - core/config.py; logic/llm_manager.py.
  - Keep reusable prefixes stable. Select routing/retention settings only when supported by the selected model and Chat Completions endpoint; measure cache reads/writes and total cost with human idle time. Do not pad prompts to seek cache eligibility or transfer llama.cpp flags to OpenAI.
  - Acceptance: unsupported options are omitted and cold/expired caches remain correct. Cached input still consumes context. [OpenAI prompt caching guidance](https://developers.openai.com/api/docs/guides/prompt-caching).

- [ ] **P2 - Use a compact fact ledger plus recent rounds for narrative memory** - core/schemas.py; logic/engine.py; logic/llm_manager.py.
  - Maintain authoritative players, world, NPCs, resources and unresolved threads, applying validated changes from the existing resolution where feasible. Keep rich prose in transcripts/UI instead of retransmitting it indefinitely.
  - Separate immutable premise from changing facts; replace superseded facts and retrieve archived details when relevant. Freeze checkpoints between compactions when practical to preserve prefix reuse. Avoid adding mandatory summarization every round.
  - Acceptance: long-session comparison against current history uses fewer total tokens without regressions in identity, possessions, injuries, causal consistency or unresolved quests.

- [ ] **P2 - Reduce planner and output tokens without degrading adjudication** - logic/llm_manager.py:plan_dice, generate_resolution; core/schemas.py; config.yaml.
  - Planner currently receives the full narrative system prompt. Trial a concise planner prompt with necessary facts, bounded short outcomes and nonduplicative public state; avoid generating new titles the engine discards.
  - Compare two-call behavior against safe deterministic handling of routine actions. A one-call experiment could supply server-generated candidate rolls and let the model select checks, but must evaluate selection bias; never let the LLM invent authoritative rolls.
  - Acceptance: compare total tokens, latency and coherent/fair outcomes. A routine-looking action must still account for contextual hazards.

- [ ] **P2 - Cache message token counts and tune compaction cadence** - logic/llm_manager.py:\_context_size, \_bounded_messages, \_compact_if_needed.
  - P1 removed FIFO deletion. Remaining: repeated history counting and backend template/tokenizer calls while searching summary prefixes; system_prompt_tokens is retained only as an initial estimate.
  - Use bounded per-message counts keyed by content/tokenizer identity plus running totals; invalidate on changes. Compact based on the upcoming request to a lower watermark, measuring summary expense and lost prefix reuse.
  - Acceptance: unchanged history is not repeatedly tokenized; totals equal a fresh calculation after append, compaction and reset. Avoid an unbounded cache of obsolete messages.

- [ ] **P2 - Validate semantics before remembering or committing LLM output** - core/schemas.py; logic/llm_manager.py:\_request; logic/engine.py:\_resolve_round.
  - Schemas accept missing/extra player names, empty narratives and empty initial titles. Raw output enters history before the engine substitutes missing resolutions, so remembered and displayed outcomes can disagree.
  - Validate participant keys, required content, dice coverage and hidden-roll membership first. Bound repair attempts and retain actions/rolls on failure instead of discarding the round.
  - Acceptance: invalid semantic output never advances state or enters memory; retries resolve the same actions with the same dice.

- [ ] **P2 - Fix stale tests and cover context/cache/lifecycle behavior** - tests/test_config_and_schemas.py, tests/test_engine.py, tests/test_server.py.
  - Remaining work: semantic output-contract coverage and stable-prefix/cache performance tests beyond the P1 regressions.
  - P1 follow-up corrected the stale title/name expectations, isolated settings, and added budget, summary, privacy, socket and lifecycle regressions. Broader semantic/cache performance coverage remains proposed.
  - Isolate settings/client creation and use fake clients/temp transcripts. Add the P1 regressions, stable-prefix request tests, and separate opt-in real-backend benchmarks.
  - Acceptance: deterministic tests run without credentials/network/downloads; mock tests are not presented as measured cache performance.

- [ ] **P2 - Bound slow-socket backpressure** - api/server.py:ConnectionManager.\_send, broadcast_global.
  - gather waits for every socket with no deadline. Failed sends only log and do not mark the player disconnected.
  - Serialize an event once, use ordered bounded per-socket delivery and timeout/disconnect handling. Acceptance: stalled receivers cannot block healthy clients or remain active turn participants indefinitely.

- [ ] **P2 - Recover missed rounds and preserve access to full history** - static/js/app.js:applySnapshot, trimContainer; logic/lobby.py:\_snapshot_locked.
  - Existing DOM prevents snapshot state replacement after disconnect. Reload only receives current state; the 500-entry cap deletes early history without a retrieval path.
  - Add public event sequence/cursor replay and paginated/virtualized history. Acceptance: reconnect restores missed events once and users can reach the opening without unbounded DOM growth or private-memory exposure.

- [ ] **P2 - Correct runtime documentation and verify dependency bounds** - README.md; pyproject.toml; api/tls_bootstrap.py.
  - P1 follow-up corrected counting/privacy/reconnect documentation and usage audience. Remaining: HTTP launch URL versus HTTPS, certificate lifetime wording and localhost SAN coverage.
  - Verify minimum dependencies support APIs used, including beta.chat.completions.parse; constrain a tested set. Acceptance: clean installation/documented startup works and counting limitations are explicit.

## Waiting On

- [ ] **Obtain a deployed backend benchmark profile** - Record llama.cpp build/model/chat template, context per slot and cache settings, or exact OpenAI model, plus typical session length. No live benchmark was run and no percentage savings are claimed.

## Someday

- [ ] **P3 - Support multiple sessions and host reset** - Isolate engines, resolvers, credentials, transcripts and cancellation before adding workers/reset. Retains earlier repository backlog intent.
- [ ] **P3 - Evaluate multilingual play** - Retains earlier translation backlog intent; assess coherence and token budgets rather than assuming a model class is required.
- [ ] **P3 - Improve transcript resilience and colors** - logic/transcript.py ignores player_colors; positional CSS changes colors when participants are omitted. Use stable colors, bounded filenames, exclusive creation and ordered writes/finalization; test retry after write failure. The unused previous_state transcript parameter and its call arguments have been removed.
- [ ] **P3 - Version static assets reproducibly** - Replace manual ?v= values with content/build hashes and suitable cache headers so unchanged assets stay cached and edits invalidate reliably.

## Done

P1 implementation validated with automated fake-model and ASGI regression tests, Black, Flake8,
and JavaScript syntax checking. No live LLM benchmark was run; model-specific coherence and cache
performance still require deployment evaluation.

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
  - Give the planner compact relevant facts/rules and private triggers, distinct from public narrative. Test that generated public prose does not disclose secret checks.
  - Acceptance: persistent injuries, locked doors and hidden hazards affect planning across quiet rounds and compaction; ordinary rolls remain public and private rolls remain private.
  - Implementation: Planner now receives genesis/private rules, durable memory and recent facts. Hidden checks stay out of public dice/transcripts, and explicit output disclosures are rejected before history commit.

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
