# Tasks

## Active
- [ ] **Fix "weaving the outcome" loading text scroll jump** - The loading message forces the Player Round Actions pane to scroll to the top, causing pointless jumping when inference completes. Show it as a popup or pinned at the bottom of the pane instead.
- [ ] **Convert logged games to readable HTML** - Convert the .logged_games transcripts to more readable HTML files (logic/transcript.py)
- [ ] **Investigate Safari login page issue** - Safari has issues getting past the login page; find the root cause
- [ ] **Show token usage approximation to host** - Make the used-tokens approximation visible to the host
- [ ] **Complete the test suite** - Add appropriate tests to finish off the test suite
- [ ] **Validate client_id format on the server** - The WebSocket path accepts any client_id string (e.g. "/" or spaces). Validate it as a UUID (or bounded charset) in `api/server.py` and reject malformed IDs.
- [ ] **Hash passwords in transit** - Auth sends the raw password over the WebSocket in plaintext. Send a SHA-256 digest of `password + client_id` instead (server re-derives it) so eavesdropping on ws:// is less useful.
- [ ] **Cap DOM growth in log and state panes** - `app.js` appends unboundedly to the log/state panes. Long sessions will grow the DOM indefinitely; trim or virtualize old entries.
- [ ] **Reconnect the WebSocket instead of reloading the page** - The `close` handler does `window.location.reload()` after 1.5s. Reopen the socket with the persisted client ID (re-auth via snapshot) instead of a full reload.
- [ ] **Enforce input length limits client-side** - Add `maxlength` to the action (4000) and chat (1000) inputs to match the server limits, avoiding late rejections.
- [ ] **Add a host end-game control** - No way to end the session; the host should be able to end the game (finalize the transcript and notify players).
- [ ] **Dim previous-round entries for readability** - Darken/dim the styling of previous rounds' entries in both the scenario-state pane and the player-actions log pane so the current round stands out (keep the latest round fully readable).
- [ ] **Cache system prompt token count** - `_context_size` re-encodes the (static) system prompt on every LLM request; precompute its token count once in `logic/llm_manager.py`.
- [ ] **Deduplicate prompt sources** - `DEFAULT_PROMPT` in `core/config.py` duplicates the system prompt in `config.yaml`; keep one source of truth.
- [ ] **Hoist payload handler map** - `GameEngine.process_payload` rebuilds its handler dict on every message; make it a class-level constant.
- [ ] **Move dm-thinking indicator out of log pane** - The loading indicator lives inside `#log-pane`, causing the scroll jump; relocate it (e.g. fixed overlay/popup) to also resolve the scroll-jump task.
- [ ] **Compact frontend state growth** - `renderedActions` Set and `playerColors` Map in `app.js` grow unboundedly; prune entries for old rounds.
- [ ] **Add DM dice-roll function** - Implement a Python function that rolls a 0-100 die. The DM AI should decide per action whether a roll is necessary (based on difficulty/plausibility); if so, the roll decides the attempt's outcome (0 = catastrophic failure, 100 = resounding success). The roll result must be visible in the player action pane, and the system prompt needs instructions on how to call the function and interpret its result.
  - Exception: if the roll concerns an event stemming from the host's hidden DM guidance (which is never shown to players), the roll itself must NOT be visible in the player action pane — only the narrative outcome is shown.
- [ ] **Drop redundant roster broadcast after auth** - `auth_ok` snapshot already includes the player list; the extra `player_roster` broadcast after authentication is redundant for the joining client.

## Waiting On

## Someday

## Done
