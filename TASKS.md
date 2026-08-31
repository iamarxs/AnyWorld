# Tasks

## Active

- [ ] After the host has entered the scenario, the AI fails to generate a descriptive name for the session, instead telling the host that “Untitled Session” is ready. Only after the game is started is the name generated.
- [ ] class \_NonSuccessOnly(logging.Filter) in app.py does not work. All "200 OK" events are logged to the console. Two examples of logged lines:
      INFO: 127.0.0.1:59560 - "GET / HTTP/1.1" 200 OK
      INFO: 127.0.0.1:59560 - "GET /static/css/style.css?v=8 HTTP/1.1" 200 OK
- [ ] Add more logging.
  - logic/llm_manager.py should log when set_genesis, generate_initial_state, generate_start_state, plan_dice or generate_resolution is run. Also, compacting the context window should be logged. If the AI backend returns any useful statistics after a query, they should be logged.
  - api/tls_bootstrap.py should log when certificates need to be renewed.
  - Make token usage visible to all players.
  - Game transcripts should use the same player colors as the player round actions pane, to give it a more lively look.
  - Add the game name in front of the scenario name: Anyworld - (scenario name)

## Waiting On

## Someday

- [ ] Support multiple concurrent game sessions on one server instance (separate engines, ports, or session IDs)
- [ ] Host reset: allow the host to clear all game state so players must re-authenticate and a new scenario can be created
- [ ] Add translations. Non-English games should be reserved for frontier models.

## Done

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
