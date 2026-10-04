"use strict";
// Storage can be unavailable in privacy modes. Keep the active tab usable in memory.
function readStored(storageName, key) {
    try {
        return window[storageName].getItem(key);
    } catch {
        return null;
    }
}

function writeStored(storageName, key, value) {
    try {
        window[storageName].setItem(key, value);
    } catch {
        // Automatic socket reconnects can still use this tab's in-memory credentials.
    }
}

function readStoredObject(storageName, key) {
    try {
        const value = JSON.parse(readStored(storageName, key));
        return value && typeof value === "object" && !Array.isArray(value) ? value : null;
    } catch {
        return null;
    }
}

const storedId = readStored("sessionStorage", "artificialDungeonClientId");
clientSession.clientId = storedId || createClientId();
clientSession.savedAuth = readStoredObject("sessionStorage", "artificialDungeonAuth");
const savedDraft = readStoredObject("sessionStorage", "artificialDungeonDraft");
elements.actionInput.value = typeof savedDraft?.text === "string" ? savedDraft.text : "";
clientSession.pendingAction = savedDraft?.pending || null;
clientSession.draftSessionId = savedDraft?.session_id || clientSession.pendingAction?.session_id || null;

function saveDraft() {
    clientSession.draftSessionId = clientSession.sessionId;
    writeStored("sessionStorage", "artificialDungeonDraft", JSON.stringify({
        session_id: clientSession.draftSessionId,
        text: elements.actionInput.value, pending: clientSession.pendingAction,
    }));
}

function acceptAction(payload) {
    const pending = clientSession.pendingAction;
    if (pending && payload.session_id === pending.session_id &&
        payload.round_number === pending.round_number && payload.action_id === pending.action_id) {
        if (elements.actionInput.value.trim() === pending.action) elements.actionInput.value = "";
        clientSession.pendingAction = null;
        saveDraft();
    }
}

function createClientId() {
    return window.crypto.randomUUID();
}

writeStored("sessionStorage", "artificialDungeonClientId", clientSession.clientId);

function identityKey(name) {
    return `artificialDungeonIdentity:${name.trim().toLowerCase()}`;
}

function rememberAuth(auth) {
    clientSession.savedAuth = auth;
    writeStored("sessionStorage", "artificialDungeonAuth", JSON.stringify(auth));
}

function rememberedIdentity(name) {
    const identity = readStoredObject("localStorage", identityKey(name));
    if (typeof identity?.clientId !== "string" ||
        !/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/.test(identity.clientId) ||
        typeof identity.reconnectToken !== "string" || !identity.reconnectToken) {
        return null;
    }
    return identity;
}

async function passwordDigest(password, identity = clientSession.clientId) {
    const bytes = new TextEncoder().encode(password + identity);
    const digest = await window.crypto.subtle.digest("SHA-256", bytes);
    return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}
