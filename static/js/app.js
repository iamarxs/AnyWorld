"use strict";

const storedId = sessionStorage.getItem("artificialDungeonClientId");
const clientId = storedId || crypto.randomUUID();
sessionStorage.setItem("artificialDungeonClientId", clientId);

const socketScheme = window.location.protocol === "https:" ? "wss" : "ws";
const ws = new WebSocket(`${socketScheme}://${window.location.host}/ws/${clientId}`);

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
    chatMessages: document.getElementById("chat-messages"),
    chatForm: document.getElementById("chat-form"),
    chatInput: document.getElementById("chat-input"),
    actionForm: document.getElementById("input-pane"),
    actionInput: document.getElementById("action-input"),
    connectionStatus: document.getElementById("connection-status"),
};

let authenticated = false;
let isHost = false;
let lastStartedRound = 0;
const renderedActions = new Set();

function send(eventType, data) {
    if (ws.readyState !== WebSocket.OPEN) {
        showError("The server connection is not open.");
        return false;
    }
    ws.send(JSON.stringify({ event_type: eventType, data }));
    return true;
}

function appendText(container, text, className) {
    const entry = document.createElement("p");
    entry.className = className;
    entry.textContent = text;
    container.appendChild(entry);
    container.scrollTop = container.scrollHeight;
}

function startRound(roundNumber) {
    if (!Number.isInteger(roundNumber) || roundNumber <= lastStartedRound) {
        return;
    }
    lastStartedRound = roundNumber;
    appendText(elements.log, `Round ${roundNumber}:`, "round-heading");
}

function showAction(roundNumber, playerName, action) {
    const actionKey = `${roundNumber}:${playerName}:${action}`;
    if (renderedActions.has(actionKey)) {
        return;
    }
    renderedActions.add(actionKey);
    startRound(roundNumber);
    appendText(elements.log, `${playerName} attempts: ${action}`, "action-entry");
}

function syncActions(roundNumber, submittedActions = {}) {
    Object.entries(submittedActions).forEach(([playerName, action]) => {
        showAction(roundNumber, playerName, action);
    });
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
    elements.state.scrollTop = elements.state.scrollHeight;
}

function showError(message) {
    if (!authenticated) {
        elements.loginError.textContent = message;
    } else {
        appendText(elements.chatMessages, `Error: ${message}`, "chat-entry error");
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
    elements.identity.textContent = `${payload.name}${isHost ? " (Host)" : ""}`;
    if (payload.scenario_title) {
        elements.title.textContent = payload.scenario_title;
    }
    elements.state.replaceChildren();
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
        showAction(payload.round_number, payload.player_name, payload.action);
    } else if (type === "state_update") {
        if (payload.round_title) {
            elements.title.textContent = payload.round_title;
        }
        startRound(payload.round_number);
        syncActions(payload.round_number, payload.submitted_actions);
        appendState(payload.global_narrative, payload.round_number);
        Object.entries(payload.player_resolutions).forEach(([player, resolution]) => {
            appendText(elements.log, `[${player}] ${resolution}`, "resolution-entry");
        });
    } else if (type === "chat_echo") {
        appendText(elements.chatMessages, `${payload.name}: ${payload.chat}`, "chat-entry");
    } else if (type === "system_msg") {
        appendText(elements.chatMessages, `System: ${payload.msg}`, "chat-entry");
        if (payload.msg === "The game has started.") {
            elements.hostModal.hidden = true;
        }
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

ws.addEventListener("open", () => {
    elements.connectionStatus.textContent = "Connected";
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
    elements.connectionStatus.textContent = "Disconnected — reload to reconnect";
    elements.actionInput.disabled = true;
    elements.chatInput.disabled = true;
});

ws.addEventListener("error", () => {
    elements.connectionStatus.textContent = "Connection error";
});

elements.loginForm.addEventListener("submit", (event) => {
    event.preventDefault();
    elements.loginError.textContent = "";
    send("auth", {
        name: elements.name.value.trim(),
        password: elements.password.value,
    });
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
