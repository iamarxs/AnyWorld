"use strict";
let logBatch = null;

function followingLatest(container) {
    return !Number.isFinite(container.scrollHeight) ||
        container.scrollHeight - container.scrollTop - container.clientHeight < 64;
}

function appendEntry(container, entry, maximum) {
    if (container === elements.log && logBatch) {
        logBatch.fragment.appendChild(entry);
        return;
    }
    const follow = followingLatest(container);
    container.appendChild(entry);
    trimContainer(container, maximum);
    if (follow) container.scrollTop = container.scrollHeight;
}

function batchRound(render) {
    logBatch = { fragment: document.createDocumentFragment(), follow: followingLatest(elements.log) };
    try { render(); } finally {
        elements.log.appendChild(logBatch.fragment);
        trimContainer(elements.log, MAX_LOG_ENTRIES);
        if (logBatch.follow) elements.log.scrollTop = elements.log.scrollHeight;
        logBatch = null;
    }
}

function renderResolution(payload) {
    const round = payload.round_number;
    if (round && clientSession.renderedRounds.has(round)) return;
    if (round) clientSession.renderedRounds.add(round);
    batchRound(() => {
        setPlayerOrder(payload.player_order);
        startRound(round);
        syncActions(round, payload.submitted_actions);
        appendState(payload.global_narrative, round);
        Object.entries(payload.dice_results || {}).forEach(([player, roll]) => {
            appendText(elements.log, `🎲 ${player} rolled ${roll}/100`,
                `dice-entry current-round${playerColorClass(player)}`);
        });
        Object.entries(payload.player_resolutions || {}).forEach(([player, resolution]) => {
            appendText(elements.log, `[${player}] ${resolution}`,
                `resolution-entry current-round${playerColorClass(player)}`);
        });
    });
    markRoundComplete();
}
function trimContainer(container, maximum) {
    while (container.children.length > maximum) {
        const removed = container.firstElementChild?.id === "game-banner"
            ? container.firstElementChild.nextElementSibling : container.firstElementChild;
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
    appendEntry(container, entry, maximum);
    return entry;
}

function startRound(roundNumber) {
    if (!Number.isInteger(roundNumber) || roundNumber <= clientSession.lastStartedRound) {
        return;
    }
    clientSession.lastStartedRound = roundNumber;
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
    const connected = players.filter((player) => player.connected).length;
    elements.lobbyPlayerCount.textContent = `${connected} of ${players.length} players connected (including host)`;
    [elements.playerList, elements.lobbyPlayerList].forEach((list) => {
        list.replaceChildren();
        players.forEach((player, index) => {
            playerColors.set(player.name, index % 8);
            const item = document.createElement("li");
            item.className = player.connected ? "player-online" : "player-offline";
            const dot = document.createElement("span");
            dot.className = "presence-dot";
            dot.setAttribute("aria-hidden", "true");
            const name = document.createElement("span");
            name.textContent = `${player.name}${player.is_host ? " · Host" : ""}`;
            const status = document.createElement("span");
            status.className = "player-presence-label";
            status.textContent = player.connected ? "Connected" : "Disconnected";
            item.append(dot, name, status);
            list.appendChild(item);
        });
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

function displayGameTitle(title) {
    return title ? `Anyworld - ${title}` : "Anyworld";
}

function appendScenario(scenario, original = false) {
    const scenarioClass = original ? "original-scenario" : "opening-scenario";
    if (!scenario || elements.log.querySelector(`.${scenarioClass}`)) {
        return;
    }
    const entry = document.createElement("article");
    entry.className = `state-entry opening-entry ${scenarioClass}`;
    const label = document.createElement("strong");
    label.className = "state-round-label";
    label.textContent = original ? "Host-typed scenario prompt" : "Opening scenario";
    const narrative = document.createElement("p");
    narrative.className = "state-narrative";
    narrative.textContent = scenario;
    entry.append(label, narrative);
    const anchor = (!original && elements.log.querySelector(".original-scenario"))
        || document.getElementById("game-banner");
    anchor.after(entry);
    trimContainer(elements.log, MAX_LOG_ENTRIES);
}

function appendState(text, roundNumber = null) {
    const entry = document.createElement("article");
    entry.className = "state-entry";
    entry.classList.add(roundNumber === null ? "opening-entry" : "current-round");
    if (roundNumber === null) entry.classList.add("opening-scenario");

    const label = document.createElement("strong");
    label.className = "state-round-label";
    label.textContent = roundNumber ? `Round ${roundNumber} result` : "Opening scenario";

    const narrative = document.createElement("p");
    narrative.className = "state-narrative";
    narrative.textContent = text;

    entry.append(label, narrative);
    appendEntry(elements.log, entry, MAX_LOG_ENTRIES);
}

function showError(message) {
    if (!clientSession.authenticated) {
        elements.loginModal.hidden = false;
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

function showScenarioError(message) {
    elements.hostModal.hidden = false;
    elements.scenarioStep.hidden = false;
    elements.lobbyStep.hidden = true;
    elements.hostStatus.textContent = message;
    elements.hostStatus.classList.add("error");
}

function showHostStep(state) {
    if (!clientSession.isHost || ["ACTIVE_TURN", "AWAITING_LLM", "ENDED"].includes(state)) {
        elements.hostModal.hidden = true;
        return;
    }
    elements.hostModal.hidden = false;
    const scenarioReady = state === "AWAITING_PLAYERS";
    elements.scenarioStep.hidden = scenarioReady;
    elements.lobbyStep.hidden = !scenarioReady;
}

function applyTurn(activePlayerId, activePlayerName) {
    const ownTurn = activePlayerId === clientSession.clientId;
    elements.actionInput.disabled = !ownTurn;
    elements.actionInput.placeholder = ownTurn
        ? "Enter your action..."
        : `Waiting for ${activePlayerName || "the next turn"}...`;
    if (ownTurn) {
        elements.actionInput.focus();
    }
}

function applySnapshot(payload) {
    clientSession.isHost = payload.is_host;
    elements.retryRoundButton.hidden = !clientSession.isHost || !payload.round_paused;
    elements.actionInput.disabled = true;
    setThinking(payload.state === "AWAITING_LLM" && !payload.round_paused);
    setPlayerOrder(payload.player_order);
    renderPlayers(payload.players);
    elements.identity.textContent = `${payload.name}${clientSession.isHost ? " (Host)" : ""}`;
    if (payload.scenario_title) {
        elements.title.textContent = displayGameTitle(payload.scenario_title);
    }
    const emptyLog = !elements.log.querySelector(":scope > :not(#game-banner)");
    appendScenario(payload.original_scenario, true);
    appendScenario(payload.opening_scenario);
    if (payload.latest_round && !clientSession.replaying) {
        renderResolution(payload.latest_round);
    } else if (emptyLog && !clientSession.replaying) {
        if (payload.completed_round_number && payload.scenario_state) {
            appendState(payload.scenario_state, payload.completed_round_number);
        }
    }
    if (payload.round_number && !clientSession.replaying) {
        startRound(payload.round_number);
        syncActions(payload.round_number, payload.submitted_actions);
    }
    if (payload.state === "ACTIVE_TURN") {
        applyTurn(payload.active_player_id, payload.active_player_name);
    }
    showHostStep(payload.state);
    elements.endGameButton.hidden = !clientSession.isHost || !["ACTIVE_TURN", "AWAITING_LLM"].includes(payload.state);
    elements.newGameButton.hidden = !clientSession.isHost || payload.state !== "ENDED";
}
