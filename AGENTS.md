# ArtificialDungeon - An AI driven RPG text adventure game over HTTP

Implement an asynchronous, multiplayer text-based RPG framework where an LLM orchestrates narrative and mechanics via a WebSocket-based
protocol. The idea is that the host launches the HTTP-based server, enters the scenario, waits for players to join (password-protected), then starts
the game when everyone is ready. The game asks every user (use the joining order) their action as a free-form text. After all users have entered
their actions, the input is sent to an LLM (OpenAI-compatible server, llama.cpp in this case. This is not in scope, it will be run separately). The
AI decides what happens based on what players decided to do, and forms a response. All players get a short paragraph of what exactly happens during
their action, then a complete scenario state is provided, and the next round begins.
- The first player to join is the host. He gets to write the scenario, and after this, he moves to a common waiting room where players can join.
- The rest of the players are given a login screen which asks for a password and their players' name.
- When the host starts the game, all players move to a main screen.
- The main game screen should consist of:
    - 3% of the top of the screen for the scenario title (LLM generated from the host's description)
    - 20% of the left of the screen for system messages (players disconnecting, rejoining etc.) and player communication with each other, that will not be sent to the AI. It is never blocked.
    - 25% of the top of the screen, below the title bar for the current scenario state.
    - 5% of the bottom of the screen for the textbox where the player can input their action on their turn. This is blocked when it is not their turn.
    - The rest of the space between the textbox and the scenario state is reserved for a scrolling text box in which LLM's generated responses are sent. It should be scrollable to the start of the game.
- A YAML config should contain the OpenAI-compatible configs, the game server's configs (host, port, password) and the system prompt, instructing the AI to direct the game fluently and creatively.

- Suggested Python libraries:
    - OpenAI (LLM backend)
    - fastapi (HTTP + WS server)
    - uvicorn (HTTP + WS server)
    - jinja2 (HTTP + WS server)
    - pydantic (Schema validation and serialization) 
    - pyyaml

# System Architecture & Task Delegation Specification

This specification decomposes the asynchronous, LLM-orchestrated RPG framework into granular, autonomous agent-actionable nodes. Each module explicitly defines its state invariants, inter-module data contracts, dependencies, and precise implementation directives to facilitate independent execution by specialized generative agents.

---

## Module 1: Configuration & Environment (`config.py`)
**Objective:** Instantiate the foundational configuration state from a YAML source into validated Pydantic models.
*   **Target File:** `core/config.py`
*   **Dependencies:** `pyyaml`, `pydantic`, `pydantic-settings`
*   **Agent Directives:**
    1.  Define a schema for `llm` (endpoint, api_key, context_window_size, model_name).
    2.  Define a schema for `server` (host, port, host_password, player_password, max_players).
    3.  Implement a dynamic loader that parses `config.yaml` and initializes a global `Settings` singleton.
*   **Acceptance Criteria:** `Settings` object raises explicit validation errors on malformed YAML. Exposes strongly-typed attributes (e.g., `Settings.server.port`).

---

## Module 2: Data Contracts & Schemas (`schemas.py`)
**Objective:** Define the rigid topological boundaries for Inter-Process Communication (IPC) via WebSockets and LLM I/O.
*   **Target File:** `core/schemas.py`
*   **Dependencies:** `pydantic`
*   **Agent Directives:**
    1.  **WebSocket Ingress (Client to Server):** `ClientPayload` containing `event_type` (`auth`, `chat`, `action`, `scenario_init`) and arbitrary `data`.
    2.  **WebSocket Egress (Server to Client):** `ServerEvent` containing `type` (`state_update`, `chat_echo`, `turn_directive`, `error`) and `payload`.
    3.  **LLM Structured Output:** `RoundResolution` model enforcing:
        *   `round_title`: `str | None`
        *   `global_narrative`: `str`
        *   `player_resolutions`: `dict[str, str]` (Mapping player IDs to their discrete action outcomes).
*   **Acceptance Criteria:** All classes inherit from `pydantic.BaseModel`. Strict validation mode enabled.

---

## Module 3: Transport Layer & Connection Gateway (`server.py`)
**Objective:** Maintain multiplexed WebSocket lifecycles and HTTP routing.
*   **Target File:** `api/server.py`
*   **Dependencies:** `fastapi`, `uvicorn`, `jinja2`
*   **Agent Directives:**
    1.  Initialize `FastAPI` application instance.
    2.  Implement `GET /` returning the Jinja2 rendered `index.html`.
    3.  Implement `WebSocket /ws/{client_id}` endpoint.
    4.  Implement a `ConnectionManager` class to store active WebSockets (`dict[str, WebSocket]`).
    5.  Implement asynchronous broadcast (`broadcast_global`) and unicast (`send_personal`) methods.
*   **Acceptance Criteria:** Handles concurrent client connections without blocking. Safely catches and logs `WebSocketDisconnect` exceptions without crashing the ASGI loop.

---

## Module 4: State Machine & Turn Orchestrator (`engine.py`)
**Objective:** Enforce the Deterministic Finite Automaton (DFA) representing game execution and resolve turn sequencing.
*   **Target File:** `logic/engine.py`
*   **Dependencies:** `schemas.py`, `server.py`
*   **Agent Directives:**
    1.  Define `GameState` Enum: `AWAITING_HOST`, `AWAITING_PLAYERS`, `SCENARIO_INJECTION`, `ACTIVE_TURN`, `AWAITING_LLM`.
    2.  Maintain a sequential turn queue (`collections.deque`) representing the strict join order.
    3.  **Input Collection:** Aggregate incoming player actions. If a player is the active node in the queue, accept their input, append to the `RoundBuffer`, and advance the pointer to the next player.
    4.  **Disconnect Handling:** If the active player drops (`is_connected == False`), auto-inject `[SYSTEM INJECTION: Player disconnected. Idle.]` and advance the pointer.
    5.  **Trigger Condition:** Once the `RoundBuffer` length equals the number of registered players, transition to `AWAITING_LLM` and invoke Module 5.
*   **Acceptance Criteria:** Strict sequential enforcement. Out-of-turn inputs are explicitly rejected. DFA transitions are immutable and atomic.

---

## Module 5: LLM Inference & Context Manager (`llm_manager.py`)
**Objective:** Handle stochastic text generation, rolling context window mutation, and schema coercion.
*   **Target File:** `logic/llm_manager.py`
*   **Dependencies:** `openai` (AsyncClient), `schemas.py`
*   **Agent Directives:**
    1.  Instantiate `AsyncOpenAI` client pointing to the configured endpoint (e.g., local `llama.cpp` server).
    2.  **Context Window Implementation:** Maintain a persistent list of messages.
        *   Index 0: System Prompt.
        *   Index 1: Initial Scenario (Host injection).
        *   Index 2 to $K$: Sliding window of historical `RoundBuffer` state and `RoundResolution` outputs. Evict oldest pairs when $N > K$.
    3.  **Inference Execution:** Wrap the API call in an asynchronous coroutine. Pass the `RoundResolution` schema to the `response_format` parameter (OpenAI structured outputs).
*   **Acceptance Criteria:** Emits purely validated `RoundResolution` Pydantic objects. Never exceeds token bounds (FIFO context eviction operates correctly).

---

## Module 6: Frontend Layout & Client Engine (`index.html`, `app.js`)
**Objective:** Establish DOM topology and state reconciliation based on the CSS Grid structural mandate.
*   **Target Files:** `templates/index.html`, `static/css/style.css`, `static/js/app.js`
*   **Dependencies:** Vanilla JS / HTML5 / CSS Grid
*   **Agent Directives:**
    1.  **CSS Grid Mapping:**
        *   `grid-template-areas: "chat title" "chat state" "chat log" "chat input";`
        *   Strict viewport constraint (`100vh`, `overflow: hidden` on parent).
    2.  **WebSocket Handshake:** Initiate connection on load. Prompt for username/password via modal before rendering the primary grid.
    3.  **State Reconciliation (JS):**
        *   Listen for `turn_directive`. If `active_player == self`, remove `disabled` attribute from the 5% bottom input box. Else, set `disabled = true`.
        *   Listen for `state_update`. Append `RoundResolution.global_narrative` to the Top Right (25%) pane. Append `RoundResolution.player_resolutions` to the Scrolling Log.
        *   Listen for `chat_echo`. Append directly to the 20% Left pane. Do not trigger global state DOM re-renders.
*   **Acceptance Criteria:** DOM elements perfectly map to the requested percentages (3%, 20%, 25%, 5%). Input field correctly locks/unlocks based on the sequential turn orchestrator's broadcast.

## Module 7: Game Logging & Audit Trail (`engine.py` integration)
**Objective:** Persist complete human-readable game transcripts for replayability and LLM evaluation.
* **Target File:** `logic/engine.py`
* **Dependencies:** `json`, `pathlib`, `datetime`
* **Agent Directives:**
    1.  Ensure `.logged_games/` directory exists upon engine instantiation.
    2.  Generate a unique session identifier in the format YYYY-MM-DD
    3.  Append round data dynamically to `.logged_games/{id}-{scenario title}.txt`
    4.  Log payload must strictly map: `Round: {round_nr}{newline}State: {scenario_state}Player actions:{newline}Player 1{player_1_action}{newline}Player 2: {player_2_action}[etc..]{results of player actions in similar format to player actions}{newline}{newline}Round: {round_nr}{newline}{new_scenario state} in plain text.
* **Acceptance Criteria:** A valid txt file is generated per session containing sequential deterministic round states without blocking the event loop.