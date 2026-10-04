"use strict";
const socketScheme = window.location.protocol === "https:" ? "wss" : "ws";
// WAN routes and reverse proxies can drop an initial handshake. Retry the socket
// without requiring the user to reload the login page manually.

function send(eventType, data) {
    if (clientSession.ws.readyState !== WebSocket.OPEN) {
        showError("The server connection is not open.");
        return false;
    }
    clientSession.ws.send(JSON.stringify({ event_type: eventType, data }));
    return true;
}

function connectSocket() {
    resetJournalRequests();
    clearTimeout(clientSession.reconnectTimer);
    clearTimeout(clientSession.connectionTimer);
    const previous = clientSession.ws;
    const socket = new WebSocket(`${socketScheme}://${window.location.host}/ws/${clientSession.clientId}`);
    clientSession.ws = socket;
    clientSession.authenticated = false;
    elements.actionInput.disabled = true;
    elements.chatInput.disabled = true;
    if (previous && previous.readyState < WebSocket.CLOSING) previous.close();
    clientSession.connectionTimer = window.setTimeout(() => {
        if (clientSession.ws === socket && socket.readyState === WebSocket.CONNECTING) socket.close();
    }, 10000);
    socket.addEventListener("open", () => {
        if (clientSession.ws !== socket) return;
        clearTimeout(clientSession.connectionTimer);
        elements.connectionStatus.textContent = clientSession.savedAuth ? "Rejoining..." : "Connected";
        if (clientSession.savedAuth) {
            socket.send(JSON.stringify({ event_type: "auth", data: clientSession.savedAuth }));
        }
    });
    socket.addEventListener("message", (event) => {
        if (clientSession.ws !== socket) return;
        try {
            handleMessage(JSON.parse(event.data));
        } catch (error) {
            console.error("Invalid server message", error);
            showError("Received an invalid server message.");
        }
    });
    socket.addEventListener("close", (event = {}) => {
        if (clientSession.ws !== socket) return;
        clearTimeout(clientSession.connectionTimer);
        clientSession.authenticated = false;
        resetJournalRequests();
        elements.connectionStatus.textContent = "Reconnecting...";
        elements.actionInput.disabled = true;
        elements.chatInput.disabled = true;
        if (event.code === 4001 || event.code === 4002 || event.code === 1008) {
            clientSession.replaced = event.code === 4001;
            elements.reclaimButton.hidden = !clientSession.replaced;
            elements.connectionStatus.textContent = clientSession.replaced
                ? "Replaced by another tab. Reclaim explicitly to reconnect." : "Authentication required.";
            if (!clientSession.replaced) {
                rememberAuth(null);
                elements.loginModal.hidden = false;
                if (event.code === 4002) {
                    elements.grid.hidden = true;
                    elements.hostModal.hidden = true;
                    elements.historyModal.hidden = true;
                    elements.newGameButton.hidden = true;
                    elements.endGameButton.hidden = true;
                    elements.retryRoundButton.hidden = true;
                    setThinking(false);
                    elements.connectionStatus.textContent = "Disconnected for a new game.";
                    elements.loginError.textContent = "The host is creating a new game. Join again when the scenario is ready.";
                }
            }
            return;
        }
        const delay = Math.round(Math.min(1000 * (2 ** clientSession.reconnectAttempts), 15000)
            * (0.8 + Math.random() * 0.4));
        clientSession.reconnectAttempts += 1;
        clientSession.reconnectTimer = window.setTimeout(connectSocket, delay);
    });
    socket.addEventListener("error", () => {
        if (clientSession.ws !== socket) return;
        elements.connectionStatus.textContent = "Connection error";
    });
}
