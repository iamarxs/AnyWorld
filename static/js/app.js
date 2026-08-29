"use strict";

const storedId = sessionStorage.getItem("artificialDungeonClientId");

// crypto.randomUUID() is unavailable on non-secure HTTP origins except localhost.
// Use a standards-compatible fallback so remote HTTP clients do not fail before
// the WebSocket is even created.
function createClientId() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
        return window.crypto.randomUUID();
    }
    if (window.crypto && typeof window.crypto.getRandomValues === "function") {
        const bytes = new Uint8Array(16);
        window.crypto.getRandomValues(bytes);
        bytes[6] = (bytes[6] & 0x0f) | 0x40;
        bytes[8] = (bytes[8] & 0x3f) | 0x80;
        const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0"));
        return `${hex.slice(0, 4).join("")}-${hex.slice(4, 6).join("")}-${hex.slice(6, 8).join("")}-${hex.slice(8, 10).join("")}-${hex.slice(10).join("")}`;
    }
    const randomHex = () => Math.floor(Math.random() * 16).toString(16);
    return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (character) => {
        const value = character === "x" ? Number.parseInt(randomHex(), 16) :
            (Number.parseInt(randomHex(), 16) & 0x3) | 0x8;
        return value.toString(16);
    });
}

const clientId = storedId || createClientId();
sessionStorage.setItem("artificialDungeonClientId", clientId);

const socketScheme = window.location.protocol === "https:" ? "wss" : "ws";
// WAN routes and reverse proxies can drop an initial handshake. Retry the socket
// without requiring the user to reload the login page manually.
let ws;
let connectionTimer;
let reconnectAttempts = 0;
let reconnectTimer;

const elements = {
    grid: document.getElementById("grid-container"),
    loginModal: document.getElementById("login-modal"),
    loginForm: document.getElementById("login-form"),
    loginError: document.getElementById("login-error"),
    name: document.getElementById("name-input"),
    password: document.getElementById("password-input"),
    hostModal: document.getElementById("host-modal"),
    scenarioStep: document.getElementById("scenario-step"),
    scenarioForm: document.getElementById("scenario-form"),
    scenario: document.getElementById("scenario-input"),
    guidance: document.getElementById("guidance-input"),
    lobbyStep: document.getElementById("lobby-step"),
    startButton: document.getElementById("start-button"),
    hostStatus: document.getElementById("host-status"),
    title: document.getElementById("scenario-title"),
    identity: document.getElementById("player-identity"),
    state: document.getElementById("state-pane"),
    log: document.getElementById("log-pane"),
    playerList: document.getElementById("player-list"),
    chatMessages: document.getElementById("chat-messages"),
    chatForm: document.getElementById("chat-form"),
    chatInput: document.getElementById("chat-input"),
    actionForm: document.getElementById("input-pane"),
    actionInput: document.getElementById("action-input"),
    dmThinking: document.getElementById("dm-thinking"),
    connectionStatus: document.getElementById("connection-status"),
    tokenUsage: document.getElementById("token-usage"),
    tokenChart: document.getElementById("token-chart"),
    tokenCount: document.getElementById("token-count"),
    endGameButton: document.getElementById("end-game-button"),
};

let authenticated = false;
let isHost = false;
let lastStartedRound = 0;
const renderedActions = new Set();
const playerColors = new Map();
const MAX_LOG_ENTRIES = 500;
const MAX_STATE_ENTRIES = 100;
const MAX_CHAT_ENTRIES = 300;

function send(eventType, data) {
    if (ws.readyState !== WebSocket.OPEN) {
        showError("The server connection is not open.");
        return false;
    }
    ws.send(JSON.stringify({ event_type: eventType, data }));
    return true;
}

function trimContainer(container, maximum) {
    while (container.children.length > maximum) {
        const removed = container.firstElementChild;
        if (removed && removed.dataset.actionKey) {
            renderedActions.delete(removed.dataset.actionKey);
        }
        removed?.remove();
    }
}

function appendText(container, text, className, maximum = MAX_LOG_ENTRIES) {
    const entry = document.createElement("p");
    entry.className = className;
    entry.textContent = text;
    container.appendChild(entry);
    trimContainer(container, maximum);
    container.scrollTop = container.scrollHeight;
    return entry;
}

function startRound(roundNumber) {
    if (!Number.isInteger(roundNumber) || roundNumber <= lastStartedRound) {
        return;
    }
    lastStartedRound = roundNumber;
    elements.log.querySelectorAll(".current-round").forEach((entry) => {
        entry.classList.remove("current-round");
    });
    appendText(elements.log, `Round ${roundNumber}:`, "round-heading current-round");
}

function markRoundComplete() {
    elements.log.querySelectorAll(".latest-complete-round").forEach((entry) => {
        entry.classList.remove("latest-complete-round");
    });
    elements.log.querySelectorAll(".current-round").forEach((entry) => {
        entry.classList.add("latest-complete-round");
    });
}

function setPlayerOrder(playerOrder = []) {
    playerOrder.forEach((playerName, index) => {
        if (!playerColors.has(playerName)) {
            playerColors.set(playerName, index % 8);
        }
    });
}

function renderPlayers(players = []) {
    elements.playerList.replaceChildren();
    players.forEach((player, index) => {
        playerColors.set(player.name, index % 8);
        const item = document.createElement("li");
        item.className = player.connected ? "player-online" : "player-offline";
        const dot = document.createElement("span");
        dot.className = "presence-dot";
        const name = document.createElement("span");
        name.textContent = `${player.name}${player.is_host ? " · Host" : ""}`;
        item.append(dot, name);
        elements.playerList.appendChild(item);
    });
}

function setThinking(active) {
    elements.dmThinking.hidden = !active;
}

function playerColorClass(playerName) {
    const colorIndex = playerColors.get(playerName);
    return colorIndex === undefined ? "" : ` player-color-${colorIndex}`;
}

function showAction(roundNumber, playerName, action, colorIndex = null) {
    if (Number.isInteger(colorIndex)) {
        playerColors.set(playerName, colorIndex % 8);
    }
    const actionKey = `${roundNumber}:${playerName}:${action}`;
    if (renderedActions.has(actionKey)) {
        return;
    }
    renderedActions.add(actionKey);
    startRound(roundNumber);
    const entry = appendText(
        elements.log,
        `${playerName} attempts: ${action}`,
        `action-entry current-round${playerColorClass(playerName)}`,
    );
    entry.dataset.actionKey = actionKey;
}

function syncActions(roundNumber, submittedActions = {}) {
    Object.entries(submittedActions).forEach(([playerName, action]) => {
        showAction(roundNumber, playerName, action);
    });
}

function appendScenario(scenario) {
    if (!scenario) {
        return;
    }
    const entry = document.createElement("article");
    entry.className = "state-entry original-scenario";
    const label = document.createElement("strong");
    label.className = "state-round-label";
    label.textContent = "Opening scenario";
    const narrative = document.createElement("p");
    narrative.className = "state-narrative";
    narrative.textContent = scenario;
    entry.append(label, narrative);
    elements.state.appendChild(entry);
}

function appendState(text, roundNumber = null) {
    elements.state.querySelector(".current-state")?.classList.remove("current-state");
    const entry = document.createElement("article");
    entry.className = "state-entry current-state";

    const label = document.createElement("strong");
    label.className = "state-round-label";
    label.textContent = roundNumber ? `Round ${roundNumber} result` : "Opening state";

    const narrative = document.createElement("p");
    narrative.className = "state-narrative";
    narrative.textContent = text;

    entry.append(label, narrative);
    elements.state.appendChild(entry);
    trimContainer(elements.state, MAX_STATE_ENTRIES);
    elements.state.scrollTop = elements.state.scrollHeight;
}

function showError(message) {
    if (!authenticated) {
        elements.loginError.textContent = message;
    } else {
        appendText(
            elements.chatMessages,
            `Error: ${message}`,
            "chat-entry error",
            MAX_CHAT_ENTRIES,
        );
    }
}

function showHostStep(state) {
    if (!isHost || state === "ACTIVE_TURN" || state === "AWAITING_LLM") {
        elements.hostModal.hidden = true;
        return;
    }
    elements.hostModal.hidden = false;
    const scenarioReady = state === "AWAITING_PLAYERS";
    elements.scenarioStep.hidden = scenarioReady;
    elements.lobbyStep.hidden = !scenarioReady;
}

function applyTurn(activePlayerId, activePlayerName) {
    const ownTurn = activePlayerId === clientId;
    elements.actionInput.disabled = !ownTurn;
    elements.actionInput.placeholder = ownTurn
        ? "Enter your action..."
        : `Waiting for ${activePlayerName || "the next turn"}...`;
    if (ownTurn) {
        elements.actionInput.focus();
    }
}

function applySnapshot(payload) {
    isHost = payload.is_host;
    setPlayerOrder(payload.player_order);
    renderPlayers(payload.players);
    elements.identity.textContent = `${payload.name}${isHost ? " (Host)" : ""}`;
    if (payload.scenario_title) {
        elements.title.textContent = payload.scenario_title;
    }
    elements.state.replaceChildren();
    appendScenario(payload.original_scenario);
    if (payload.scenario_state) {
        appendState(payload.scenario_state, payload.completed_round_number);
    }
    if (payload.round_number) {
        startRound(payload.round_number);
        syncActions(payload.round_number, payload.submitted_actions);
    }
    if (payload.state === "ACTIVE_TURN") {
        applyTurn(payload.active_player_id, payload.active_player_name);
    }
    showHostStep(payload.state);
    elements.endGameButton.hidden = !isHost || !["ACTIVE_TURN", "AWAITING_LLM"].includes(payload.state);
}

function handleMessage(message) {
    const { type, payload } = message;
    if (!authenticated && type !== "auth_ok" && type !== "error") {
        return;
    }
    if (type === "auth_ok") {
        authenticated = true;
        elements.loginModal.hidden = true;
        elements.grid.hidden = false;
        elements.loginError.textContent = "";
        applySnapshot(payload);
    } else if (type === "turn_directive") {
        startRound(payload.round_number);
        syncActions(payload.round_number, payload.submitted_actions);
        applyTurn(payload.active_player_id, payload.active_player_name);
    } else if (type === "round_start") {
        startRound(payload.round_number);
    } else if (type === "action_echo") {
        showAction(
            payload.round_number,
            payload.player_name,
            payload.action,
            payload.player_color_index,
        );
    } else if (type === "state_update") {
        setThinking(false);
        setPlayerOrder(payload.player_order);
        if (payload.round_title) {
            elements.title.textContent = payload.round_title;
        }
        startRound(payload.round_number);
        syncActions(payload.round_number, payload.submitted_actions);
        if (payload.original_scenario && !elements.state.querySelector(".original-scenario")) {
            appendScenario(payload.original_scenario);
        }
        appendState(payload.global_narrative, payload.round_number);
        Object.entries(payload.dice_results || {}).forEach(([player, roll]) => {
            appendText(
                elements.log,
                `🎲 ${player} rolled ${roll}/100`,
                `dice-entry current-round${playerColorClass(player)}`,
            );
        });
        Object.entries(payload.player_resolutions).forEach(([player, resolution]) => {
            appendText(
                elements.log,
                `[${player}] ${resolution}`,
                `resolution-entry current-round${playerColorClass(player)}`,
            );
        });
        markRoundComplete();
    } else if (type === "player_roster") {
        renderPlayers(payload.players);
    } else if (type === "dm_thinking") {
        setThinking(Boolean(payload.active));
    } else if (type === "chat_echo") {
        appendText(
            elements.chatMessages,
            `${payload.name}: ${payload.chat}`,
            "chat-entry",
            MAX_CHAT_ENTRIES,
        );
    } else if (type === "system_msg") {
        appendText(
            elements.chatMessages,
            `System: ${payload.msg}`,
            "chat-entry",
            MAX_CHAT_ENTRIES,
        );
        if (payload.msg === "The game has started.") {
            elements.hostModal.hidden = true;
        }
    } else if (type === "token_usage") {
        elements.tokenUsage.hidden = false;
        const used = Math.max(0, Number(payload.approximate_tokens) || 0);
        const limit = Math.max(1, Number(payload.context_window_size) || used || 1);
        const ratio = Math.min(1, used / limit);
        elements.tokenChart.style.background =
            `conic-gradient(var(--accent) ${ratio * 360}deg, var(--border) ${ratio * 360}deg)`;
        elements.tokenChart.setAttribute(
            "aria-label",
            `Token usage: ${used.toLocaleString()} of ${limit.toLocaleString()}`,
        );
        elements.tokenCount.textContent = `≈ ${used.toLocaleString()} / ${limit.toLocaleString()} tokens`;
    } else if (type === "game_ended") {
        setThinking(false);
        elements.actionInput.disabled = true;
        elements.endGameButton.hidden = true;
        appendText(elements.chatMessages, `System: ${payload.msg}`, "chat-entry", MAX_CHAT_ENTRIES);
    } else if (type === "scenario_ready") {
        elements.hostStatus.textContent = `“${payload.title}” is ready.`;
        elements.scenarioStep.hidden = true;
        elements.lobbyStep.hidden = false;
        elements.scenarioForm.querySelector("button").disabled = false;
    } else if (type === "error") {
        showError(payload.msg || "Unknown server error.");
        elements.scenarioForm.querySelector("button").disabled = false;
        elements.startButton.disabled = false;
    }
}

function connectSocket() {
    clearTimeout(reconnectTimer);
    ws = new WebSocket(`${socketScheme}://${window.location.host}/ws/${clientId}`);
    connectionTimer = window.setTimeout(() => {
        if (ws.readyState === WebSocket.CONNECTING) ws.close();
    }, 10000);
    ws.addEventListener("open", () => {
        clearTimeout(connectionTimer);
        reconnectAttempts = 0;
        elements.connectionStatus.textContent = "Connected";
        elements.chatInput.disabled = false;
        const savedAuth = sessionStorage.getItem("artificialDungeonAuth");
        if (savedAuth && !authenticated) {
            ws.send(JSON.stringify({ event_type: "auth", data: JSON.parse(savedAuth) }));
        }
    });
    ws.addEventListener("message", (event) => {
        try {
            handleMessage(JSON.parse(event.data));
        } catch (error) {
            console.error("Invalid server message", error);
            showError("Received an invalid server message.");
        }
    });
    ws.addEventListener("close", () => {
        authenticated = false;
        elements.connectionStatus.textContent = "Reconnecting...";
        elements.actionInput.disabled = true;
        elements.chatInput.disabled = true;
        const delay = Math.min(1000 * (2 ** reconnectAttempts), 15000);
        reconnectAttempts += 1;
        reconnectTimer = window.setTimeout(connectSocket, delay);
    });
    ws.addEventListener("error", () => {
        elements.connectionStatus.textContent = "Connection error";
    });
}

connectSocket();

function fallbackSha256(value) {
    const rightRotate = (word, amount) => (word >>> amount) | (word << (32 - amount));
    const maxWord = 2 ** 32;
    const words = [];
    const hash = [];
    const constants = [];
    const composite = {};
    let primeCounter = 0;
    for (let candidate = 2; primeCounter < 64; candidate += 1) {
        if (!composite[candidate]) {
            for (let multiple = candidate * candidate; multiple < 313; multiple += candidate) {
                composite[multiple] = true;
            }
            if (primeCounter < 8) hash[primeCounter] = (candidate ** 0.5 * maxWord) | 0;
            constants[primeCounter] = (candidate ** (1 / 3) * maxWord) | 0;
            primeCounter += 1;
        }
    }
    const encoded = unescape(encodeURIComponent(value));
    for (let index = 0; index < encoded.length; index += 1) {
        words[index >> 2] |= encoded.charCodeAt(index) << (3 - (index % 4)) * 8;
    }
    words[encoded.length >> 2] |= 0x80 << (3 - (encoded.length % 4)) * 8;
    words[((encoded.length + 8) >> 6) * 16 + 15] = encoded.length * 8;
    for (let block = 0; block < words.length; block += 16) {
        const schedule = words.slice(block, block + 16);
        const oldHash = hash.slice();
        for (let index = 0; index < 64; index += 1) {
            const w15 = schedule[index - 15];
            const w2 = schedule[index - 2];
            const a = hash[0];
            const e = hash[4];
            const temp1 = hash[7] + (rightRotate(e, 6) ^ rightRotate(e, 11) ^ rightRotate(e, 25))
                + ((e & hash[5]) ^ (~e & hash[6])) + constants[index]
                + (schedule[index] = index < 16 ? schedule[index] :
                    (schedule[index - 16] + (rightRotate(w15, 7) ^ rightRotate(w15, 18) ^ (w15 >>> 3))
                    + schedule[index - 7] + (rightRotate(w2, 17) ^ rightRotate(w2, 19) ^ (w2 >>> 10))) | 0);
            const temp2 = (rightRotate(a, 2) ^ rightRotate(a, 13) ^ rightRotate(a, 22))
                + ((a & hash[1]) ^ (a & hash[2]) ^ (hash[1] & hash[2]));
            hash.pop();
            hash.unshift((temp1 + temp2) | 0);
            hash[4] = (hash[5] + temp1) | 0;
        }
        hash.forEach((valuePart, index) => { hash[index] = (valuePart + oldHash[index]) | 0; });
    }
    return hash.map((word) => (word >>> 0).toString(16).padStart(8, "0")).join("");
}

async function passwordDigest(password) {
    const value = password + clientId;
    // Some mobile browsers expose crypto.subtle but reject it on an insecure HTTP
    // origin. Fall back if the digest operation itself is unavailable or rejected.
    if (window.crypto?.subtle && window.TextEncoder) {
        try {
            const bytes = new TextEncoder().encode(value);
            const digest = await window.crypto.subtle.digest("SHA-256", bytes);
            return Array.from(new Uint8Array(digest), (byte) =>
                byte.toString(16).padStart(2, "0"),
            ).join("");
        } catch (error) {
            console.warn("Web Crypto SHA-256 unavailable; using fallback", error);
        }
    }
    return fallbackSha256(value);
}

elements.loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    elements.loginError.textContent = "";
    try {
        const auth = {
            name: elements.name.value.trim(),
            password: elements.password.value,
            password_digest: await passwordDigest(elements.password.value),
        };
        if (send("auth", auth)) {
            sessionStorage.setItem("artificialDungeonAuth", JSON.stringify({
                name: auth.name,
                password_digest: auth.password_digest,
            }));
        }
    } catch (error) {
        showError(error.message);
    }
});

elements.scenarioForm.addEventListener("submit", (event) => {
    event.preventDefault();
    if (
        send("scenario_init", {
            scenario: elements.scenario.value.trim(),
            guidance: elements.guidance.value.trim(),
        })
    ) {
        elements.scenarioForm.querySelector("button").disabled = true;
        elements.hostStatus.textContent = "Generating the scenario...";
    }
});

elements.endGameButton.addEventListener("click", () => {
    if (window.confirm("End this game for every player?")) {
        send("end_game", {});
    }
});

elements.startButton.addEventListener("click", () => {
    if (send("start_game", {})) {
        elements.startButton.disabled = true;
        elements.hostStatus.textContent = "Starting game...";
    }
});

elements.chatForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const message = elements.chatInput.value.trim();
    if (message && send("chat", { message })) {
        elements.chatInput.value = "";
    }
});

elements.actionForm.addEventListener("submit", (event) => {
    event.preventDefault();
    const action = elements.actionInput.value.trim();
    if (action && !elements.actionInput.disabled && send("action", { action })) {
        elements.actionInput.value = "";
        elements.actionInput.disabled = true;
    }
});
