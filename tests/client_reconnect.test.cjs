const assert = require("node:assert/strict");
const { test } = require("node:test");
const { readFileSync } = require("node:fs");
const { randomUUID, createHash, webcrypto } = require("node:crypto");
const vm = require("node:vm");
const path = require("node:path");

const source = ["state", "identity", "rendering", "transport", "accessibility", "journal", "scenarios", "app"]
    .map((name) => readFileSync(path.join(__dirname, `../static/js/${name}.js`), "utf8"))
    .join("\n");

function storage() {
    const values = new Map();
    return {
        getItem: (key) => values.get(key) ?? null,
        setItem: (key, value) => values.set(key, value),
    };
}

// Run the real client script with isolated browser surfaces; no server or inference.
function browser(localStorage = storage(), sessionStorage = storage()) {
    const nodes = new Map();
    const downloads = [];
    const blobs = [];
    let createdId = 0;
    function node(id) {
        if (!nodes.has(id)) nodes.set(id, {
            hidden: id === "grid-container", disabled: false, value: "", textContent: "",
            dataset: {}, style: {}, children: [], listeners: {},
            classList: { add() {}, remove() {} },
            addEventListener(type, callback) { this.listeners[type] = callback; },
            querySelector() { return node("button"); },
            querySelectorAll() { return []; },
            append(...children) { this.children.push(...children); },
            appendChild() {}, replaceChildren(...children) { this.children = children; },
            focus() {}, after() {},
            reset() { this.resetCalled = true; },
            click() { downloads.push({ href: this.href, download: this.download }); },
            setAttribute() {}, getClientRects() { return [1]; }, contains() { return false; },
        });
        return nodes.get(id);
    }
    const sockets = [];
    class Socket {
        static CONNECTING = 0;
        static OPEN = 1;
        static CLOSING = 2;
        constructor(url) {
            this.url = url;
            this.readyState = 0;
            this.listeners = {};
            this.sent = [];
            sockets.push(this);
        }
        addEventListener(type, callback) { this.listeners[type] = callback; }
        open() { this.readyState = 1; this.listeners.open(); }
        send(data) { this.sent.push(JSON.parse(data)); }
        close(code = 1006) { this.readyState = 3; this.listeners.close?.({ code }); }
        receive(type, payload) { this.listeners.message({ data: JSON.stringify({ type, payload }) }); }
    }
    const timers = new Map();
    let timerId = 0;
    const setTimeout = (callback, delay) => {
        timers.set(++timerId, { callback, delay });
        return timerId;
    };
    const clearTimeout = (id) => timers.delete(id);
    const window = {
        location: { protocol: "https:", host: "game.test:4141" },
        crypto: { randomUUID, subtle: webcrypto.subtle }, localStorage, sessionStorage, setTimeout,
        confirm: () => true,
    };
    const runtime = {
        window, sessionStorage, WebSocket: Socket, setTimeout, clearTimeout, console, Blob, TextEncoder,
        URL: { createObjectURL(blob) { blobs.push(blob); return "blob:test"; }, revokeObjectURL() {} },
        MutationObserver: class { observe() {} },
        document: { getElementById: node, createElement: (tag) => node(`${tag}-${++createdId}`), listeners: {},
            createDocumentFragment: () => node("fragment"),
            addEventListener(type, callback) { this.listeners[type] = callback; } },
    };
    vm.runInNewContext(source, runtime);
    return {
        runtime, downloads, blobs,
        session: vm.runInNewContext("clientSession", runtime),
        runTimer(delay) {
            const found = [...timers.entries()].find(([, timer]) => timer.delay === delay);
            assert.ok(found, `Expected a timer at ${delay} ms`);
            timers.delete(found[0]);
            found[1].callback();
        },
        sockets, node, localStorage, sessionStorage,
        async login(name = "Arxs", password = "party-password") {
            node("name-input").value = name;
            node("password-input").value = password;
            await node("login-form").listeners.submit({ preventDefault() {} });
        },
        accept(socket = sockets.at(-1), name = "Arxs", token = "private-token",
            isHost = false, state = "ACTIVE_TURN") {
            socket.receive("auth_ok", {
                name, reconnect_token: token, is_host: isHost, state,
                players: [], player_order: [],
            });
        },
        reconnect() {
            const timer = [...timers.values()].find(({ delay }) => delay >= 800 && delay <= 1200);
            assert.ok(timer, "A reconnect should be scheduled");
            timer.callback();
            return sockets.at(-1);
        },
    };
}

async function joined(local, session) {
    const tab = browser(local, session);
    tab.sockets[0].open();
    await tab.login();
    tab.accept();
    return tab;
}

test("same-tab disconnect automatically reuses authenticated credentials", async () => {
    const tab = await joined();
    const first = tab.sockets[0];
    assert.match(first.url, /^wss:\/\/game\.test:4141\/ws\/[0-9a-f-]{36}$/);
    first.close();
    const next = tab.reconnect();
    assert.equal(next.url, first.url);
    next.open();
    assert.equal(next.sent[0].data.reconnect_token, "private-token");
    assert.equal(next.sent[0].data.password_digest, first.sent[0].data.password_digest);
    assert.equal(tab.node("chat-input").disabled, true);
    tab.accept(next);
    assert.equal(tab.node("login-modal").hidden, true);
    assert.equal(tab.node("chat-input").disabled, false);
});

test("replacement requires explicit reclaim and authentication rejection stops retries", async () => {
    const tab = await joined();
    tab.sockets[0].close(4001);
    assert.equal(tab.node("reclaim-button").hidden, false);
    assert.throws(() => tab.reconnect(), /reconnect should be scheduled/);
    tab.node("reclaim-button").listeners.click();
    assert.equal(tab.sockets.length, 2);
    const reclaimed = tab.sockets.at(-1);
    reclaimed.open();
    reclaimed.close(1008);
    assert.equal(tab.node("login-modal").hidden, false);
    assert.equal(JSON.parse(tab.sessionStorage.getItem("artificialDungeonAuth")), null);
});

test("draft survives a lost acknowledgment and only its scoped ID clears it", async () => {
    const tab = await joined();
    const socket = tab.sockets[0];
    socket.receive("auth_ok", { name: "Arxs", reconnect_token: "private-token", state: "ACTIVE_TURN",
        session_id: "session-a", round_number: 1, active_player_id: socket.url.split("/").at(-1),
        player_order: [], players: [] });
    tab.node("action-input").value = "Wait at the gate";
    tab.node("input-pane").listeners.submit({ preventDefault() {} });
    const sent = socket.sent.at(-1).data;
    assert.equal(sent.session_id, "session-a");
    assert.equal(tab.node("action-input").value, sent.action);
    socket.receive("action_accepted", { ...sent, round_number: 2 });
    assert.equal(tab.node("action-input").value, sent.action);
    socket.close();
    const next = tab.reconnect();
    next.open();
    next.receive("auth_ok", { name: "Arxs", reconnect_token: "private-token", state: "ACTIVE_TURN",
        session_id: "session-a", round_number: 1, active_player_id: socket.url.split("/").at(-1),
        player_order: [], players: [] });
    assert.equal(next.sent.at(-1).data.action_id, sent.action_id);
    next.receive("action_accepted", sent);
    assert.equal(tab.node("action-input").value, "");
});

test("ordered replay renders missed outcomes and public dice once despite live duplicates", async () => {
    const tab = await joined();
    const shown = [];
    tab.runtime.appendState = (text, round) => shown.push(["state", round, text]);
    tab.runtime.appendText = (container, text) => { shown.push(["text", text]); return { dataset: {} }; };
    const outcome = { session_id: "session-a", event_id: 2, round_number: 1,
        global_narrative: "Gate opens", player_resolutions: { Arxs: "Entered" }, dice_results: { Arxs: 42 } };
    tab.sockets[0].receive("auth_ok", { name: "Arxs", reconnect_token: "private-token",
        state: "ACTIVE_TURN", session_id: "session-a", latest_event_id: 3, round_number: 2,
        latest_round: outcome, players: [], player_order: [] });
    assert.equal(tab.sockets[0].sent.at(-1).event_type, "journal_request");
    const next = { ...outcome, event_id: 4, round_number: 2, global_narrative: "Entered courtyard" };
    tab.sockets[0].receive("state_update", next);
    tab.sockets[0].receive("journal_page", { session_id: "session-a", mode: "replay", cursor: 3,
        has_more: false, events: [{ type: "state_update", payload: outcome }] });
    tab.sockets[0].receive("state_update", next);
    tab.sockets[0].receive("state_update", outcome);
    assert.deepEqual(shown.filter((entry) => entry[0] === "state").map((entry) => entry[1]), [1, 2]);
    assert.equal(shown.filter((entry) => entry[1] === "🎲 Arxs rolled 42/100").length, 2);
});

test("incoming chat preserves action focus and does not pull a reader to the bottom", async () => {
    const tab = await joined();
    let focused = 0;
    tab.node("action-input").focus = () => { focused += 1; };
    tab.runtime.applyTurn(tab.sockets[0].url.split("/").at(-1), "Arxs");
    const chat = tab.node("chat-messages");
    chat.scrollHeight = 1000;
    chat.scrollTop = 10;
    chat.clientHeight = 100;
    tab.sockets[0].receive("chat_echo", { name: "Other", chat: "Hello" });
    assert.equal(focused, 1);
    assert.equal(chat.scrollTop, 10);
});

test("history modal traps Tab in both directions and restores prior focus", async () => {
    const tab = await joined();
    const document = tab.runtime.document;
    for (const id of ["login-modal", "host-modal", "history-modal"]) tab.node(id).hidden = true;
    tab.runtime.syncModalFocus();
    const action = tab.node("action-input");
    action.focus = () => { document.activeElement = action; };
    action.focus();
    const first = tab.node("history-search");
    const last = tab.node("history-close");
    first.focus = () => { document.activeElement = first; };
    last.focus = () => { document.activeElement = last; };
    const modal = tab.node("history-modal");
    modal.querySelectorAll = () => [first, last];
    modal.contains = (element) => [first, last].includes(element);
    modal.hidden = false;
    tab.runtime.syncModalFocus();
    assert.equal(document.activeElement, first);
    document.listeners.keydown({ key: "Tab", shiftKey: true, preventDefault() {} });
    assert.equal(document.activeElement, last);
    document.listeners.keydown({ key: "Tab", shiftKey: false, preventDefault() {} });
    assert.equal(document.activeElement, first);
    modal.hidden = true;
    tab.runtime.syncModalFocus();
    assert.equal(document.activeElement, action);
});

test("a reopened tab recovers its identity after name and password entry", async () => {
    const first = await joined();
    const next = browser(first.localStorage);
    const pending = next.sockets[0];
    pending.open();
    assert.equal(pending.sent.length, 0, "Persistent storage must not auto-login");
    await next.login("arxs");
    const recovered = next.sockets.at(-1);
    assert.equal(recovered.url, first.sockets[0].url);
    assert.notEqual(recovered, pending);
    recovered.open();
    assert.equal(recovered.sent[0].data.reconnect_token, "private-token");
    assert.equal(recovered.sent[0].data.password_digest, first.sockets[0].sent[0].data.password_digest);
    next.accept(recovered);
    pending.close();
    pending.receive("error", { msg: "Old socket error" });
    assert.equal(next.node("connection-status").textContent, "Connected");
    assert.equal(next.node("login-modal").hidden, true);
    const record = JSON.parse(first.localStorage.getItem("artificialDungeonIdentity:arxs"));
    assert.deepEqual(Object.keys(record).sort(), ["clientId", "reconnectToken"]);
});

test("reloading the original tab keeps automatic login", async () => {
    const first = await joined();
    const reloaded = browser(first.localStorage, first.sessionStorage);
    reloaded.sockets[0].open();
    assert.equal(reloaded.sockets[0].url, first.sockets[0].url);
    assert.equal(reloaded.sockets[0].sent[0].data.reconnect_token, "private-token");
});

test("different players in new tabs keep separate identities", async () => {
    const first = await joined();
    const other = browser(first.localStorage);
    other.sockets[0].open();
    await other.login("Other");
    assert.notEqual(other.sockets[0].url, first.sockets[0].url);
    assert.equal(other.sockets[0].sent[0].data.reconnect_token, undefined);
    other.accept(other.sockets[0], "Other", "other-token");
    assert.notEqual(first.localStorage.getItem("artificialDungeonIdentity:arxs"),
        first.localStorage.getItem("artificialDungeonIdentity:other"));
});

test("recovery binds a newly entered password to the original client ID", async () => {
    const first = await joined();
    const next = browser(first.localStorage);
    next.sockets[0].open();
    await next.login("Arxs", "wrong-password");
    const socket = next.sockets.at(-1);
    socket.open();
    const id = socket.url.split("/").at(-1);
    assert.equal(socket.sent[0].data.password_digest,
        createHash("sha256").update("wrong-password" + id).digest("hex"));
    socket.receive("error", { msg: "Invalid password for this session." });
    assert.equal(next.node("login-modal").hidden, false);
    assert.equal(next.node("chat-input").disabled, true);
});

test("blocked storage still permits login and in-memory socket recovery", async () => {
    const blocked = {
        getItem() { throw new Error("Blocked"); },
        setItem() { throw new Error("Blocked"); },
    };
    const tab = await joined(blocked, blocked);
    tab.sockets[0].close();
    const next = tab.reconnect();
    next.open();
    assert.equal(next.sent[0].data.reconnect_token, "private-token");
});

test("corrupt stored JSON does not prevent a fresh login", async () => {
    const local = storage();
    const session = storage();
    local.setItem("artificialDungeonIdentity:arxs", "not JSON");
    session.setItem("artificialDungeonAuth", "not JSON");
    const tab = await joined(local, session);
    assert.equal(tab.node("login-modal").hidden, true);
});

test("opening uses generated text and snapshots keep it separate from later rounds", async () => {
    const tab = await joined();
    const shown = [];
    tab.runtime.appendScenario = (text, original = false) => {
        if (text) shown.push([original ? "original" : "opening", text]);
    };
    tab.runtime.appendState = (text, round) => shown.push(["state", text, round]);
    tab.runtime.startRound = () => {};
    tab.runtime.syncActions = () => {};
    tab.runtime.markRoundComplete = () => {};
    tab.node("log-pane").querySelector = () => null;
    const snapshot = {
        state: "AWAITING_PLAYERS", players: [], player_order: [],
        original_scenario: "Raw host prompt", scenario_state: "Lobby preparation",
    };
    tab.runtime.applySnapshot(snapshot);
    assert.deepEqual(shown, [["original", "Raw host prompt"]]);
    shown.length = 0;
    tab.sockets[0].receive("state_update", {
        global_narrative: "Host the ranger and Player the mage arrive.", player_resolutions: {},
    });
    assert.equal(shown.length, 1);
    assert.equal(shown[0][1], "Host the ranger and Player the mage arrive.");
    shown.length = 0;
    tab.runtime.applySnapshot({
        ...snapshot, state: "ACTIVE_TURN", round_number: 1,
        opening_scenario: "Generated opening", scenario_state: "Generated opening",
    });
    assert.deepEqual(shown, [["original", "Raw host prompt"], ["opening", "Generated opening"]]);
    shown.length = 0;
    tab.runtime.applySnapshot({
        ...snapshot, state: "ACTIVE_TURN", round_number: 3, completed_round_number: 2,
        opening_scenario: "Generated opening", scenario_state: "Later state",
    });
    assert.deepEqual(shown, [
        ["original", "Raw host prompt"], ["opening", "Generated opening"], ["state", "Later state", 2],
    ]);
});

test("scenario validation errors remain visible in the host form", async () => {
    const tab = browser();
    const socket = tab.sockets[0];
    socket.open();
    await tab.login("Host");
    tab.accept(socket, "Host", "private-token", true, "SCENARIO_INJECTION");
    assert.equal(tab.node("host-modal").hidden, false);
    assert.equal(tab.node("scenario-step").hidden, false);

    tab.node("scenario-form").listeners.submit({ preventDefault() {} });
    socket.receive("dm_thinking", { active: true });
    assert.equal(tab.node("host-modal").hidden, false);
    socket.receive("error", { msg: "Freeform DM guidance cannot contain percentage events." });

    assert.equal(tab.node("host-status").textContent,
        "Freeform DM guidance cannot contain percentage events.");
    assert.equal(tab.node("scenario-form").querySelector().disabled, false);
});


test("only the ended host can restart, and a fresh session restores scenario creation", async () => {
    const tab = browser();
    const socket = tab.sockets[0];
    socket.open();
    await tab.login("Host");
    socket.receive("auth_ok", { name: "Host", reconnect_token: "host-token", is_host: true,
        state: "ACTIVE_TURN", session_id: "old-game", players: [], player_order: [] });
    assert.equal(tab.node("new-game-button").hidden, true);
    socket.receive("game_ended", { msg: "The host ended the game." });
    assert.equal(tab.node("new-game-button").hidden, false);
    tab.node("new-game-button").listeners.click();
    assert.deepEqual(socket.sent.at(-1), { event_type: "new_game", data: {} });
    assert.equal(tab.node("new-game-button").disabled, true);
    tab.node("action-input").value = "Old action";
    tab.session.pendingAction = { session_id: "old-game" };
    tab.node("start-button").disabled = true;
    socket.receive("auth_ok", { name: "Host", reconnect_token: "host-token", is_host: true,
        state: "SCENARIO_INJECTION", session_id: "new-game", players: [], player_order: [] });
    assert.equal(tab.node("host-modal").hidden, false);
    assert.equal(tab.node("scenario-step").hidden, false);
    assert.equal(tab.node("lobby-step").hidden, true);
    assert.equal(tab.node("scenario-form").resetCalled, true);
    assert.equal(tab.node("start-button").disabled, false);
    assert.equal(tab.node("new-game-button").hidden, true);
    assert.equal(tab.session.pendingAction, null);
    assert.equal(tab.node("action-input").value, "");
    assert.equal(tab.session.cursor, 0);
    assert.equal(tab.session.savedAuth.reconnect_token, "host-token");
    socket.receive("auth_ok", { name: "Host", reconnect_token: "host-token", is_host: true,
        state: "ENDED", session_id: "new-game", players: [], player_order: [] });
    assert.equal(tab.node("new-game-button").hidden, false);
    const player = await joined();
    player.sockets[0].receive("game_ended", { msg: "The host ended the game." });
    assert.equal(player.node("new-game-button").hidden, true);
});

test("a new game disconnects players without automatically rejoining", async () => {
    const tab = await joined();
    tab.sockets[0].close(4002);
    assert.equal(tab.node("login-modal").hidden, false);
    assert.equal(tab.node("grid-container").hidden, true);
    assert.equal(tab.session.savedAuth, null);
    assert.match(tab.node("login-error").textContent, /Join again/);
    assert.throws(() => tab.reconnect(), /reconnect should be scheduled/);
    await tab.login();
    assert.equal(tab.sockets.length, 2);
    tab.sockets[1].open();
    assert.equal(tab.sockets[1].sent[0].event_type, "auth");
});

for (const newSession of [false, true]) {
    test(`reload ${newSession ? "clears an old-game" : "preserves a same-game"} unsent draft`, async () => {
        const local = storage();
        const persisted = storage();
        const tab = await joined(local, persisted);
        tab.sockets[0].receive("auth_ok", { name: "Arxs", reconnect_token: "private-token",
            state: "ACTIVE_TURN", session_id: "old-game", players: [], player_order: [] });
        tab.node("action-input").value = "Use the brass key";
        tab.node("action-input").listeners.input();
        if (newSession) tab.sockets[0].close(4002);
        const reopened = browser(local, persisted);
        reopened.sockets[0].open();
        if (newSession) await reopened.login();
        const sessionId = newSession ? "new-game" : "old-game";
        reopened.sockets[0].receive("auth_ok", { name: "Arxs", reconnect_token: "fresh-token",
            state: "ACTIVE_TURN", session_id: sessionId, players: [], player_order: [] });
        const text = newSession ? "" : "Use the brass key";
        assert.equal(reopened.node("action-input").value, text);
        const draft = JSON.parse(persisted.getItem("artificialDungeonDraft"));
        assert.equal(draft.text, text);
        assert.equal(draft.session_id, sessionId);
        assert.equal(draft.pending, null);
    });
}

test("new sessions reset completed history searches and ignore late old replies", async () => {
    const tab = await joined();
    tab.sockets[0].receive("auth_ok", { name: "Host", reconnect_token: "host-token", is_host: true,
        state: "ENDED", session_id: "old-game", players: [], player_order: [] });
    tab.node("history-search").value = "old dragon";
    tab.node("history-button").listeners.click();
    tab.sockets[0].receive("journal_page", { session_id: "old-game", mode: "history",
        cursor: 120, has_more: true, events: [] });
    assert.equal(tab.session.historyCursor, 120);
    assert.equal(tab.node("history-next").disabled, false);
    tab.sockets[0].receive("auth_ok", { name: "Host", reconnect_token: "host-token", is_host: true,
        state: "SCENARIO_INJECTION", session_id: "new-game", players: [], player_order: [] });
    assert.equal(tab.session.historyCursor, 0);
    assert.equal(tab.node("history-search").value, "");
    assert.equal(tab.node("history-next").disabled, true);
    assert.equal(tab.node("history-export").disabled, false);
    tab.sockets[0].receive("journal_page", { session_id: "old-game", mode: "history",
        cursor: 240, has_more: true, events: [] });
    assert.equal(tab.session.historyCursor, 0);
    tab.node("history-button").listeners.click();
    assert.deepEqual(tab.sockets[0].sent.at(-1).data,
        { mode: "history", after: 0, limit: 100, search: "" });
});

for (const mode of ["replay", "export"]) {
    test(`${mode} resumes a rejected page beyond 3100 events and restores controls`, async () => {
        const tab = await joined();
        const socket = tab.sockets[0];
        let rendered = 0;
        tab.runtime.appendText = () => { rendered++; return { dataset: {} }; };
        socket.receive("auth_ok", { name: "Arxs", reconnect_token: "private-token",
            state: "ACTIVE_TURN", session_id: "long-session", latest_event_id: mode === "replay" ? 3101 : 0,
            players: [], player_order: [] });
        if (mode === "export") {
            tab.session.cursor = 3101;
            tab.node("history-export").listeners.click();
        }
        function page(after, end) {
            socket.receive("journal_page", { session_id: "long-session", mode, cursor: end,
                latest_event_id: 3101, has_more: end < 3101, events: Array.from({ length: end - after },
                    (_, index) => ({ type: "chat_echo", payload: { session_id: "long-session",
                        event_id: after + index + 1, name: "Arxs", chat: "Archived" } })) });
        }
        for (let after = 0; after < 3000; after += 100) page(after, after + 100);
        const request = socket.sent.at(-1).data;
        assert.equal(request.after, 3000);
        socket.receive("error", { code: "journal_rate_limited", request, retry_after_seconds: 10,
            msg: "Message rate exceeded; please wait." });
        const before = socket.sent.length;
        if (mode === "replay") {
            socket.receive("chat_echo", { session_id: "long-session", event_id: 3102,
                name: "Arxs", chat: "Live" });
            assert.equal(tab.session.liveEvents.length, 1);
            assert.equal(tab.session.replaying, true);
        }
        tab.runTimer(10050);
        assert.equal(socket.sent.length, before + 1);
        assert.deepEqual(socket.sent.at(-1).data, request);
        page(3000, 3100);
        page(3100, 3101);
        if (mode === "replay") {
            assert.equal(tab.session.replaying, false);
            assert.equal(tab.session.liveEvents.length, 0);
            assert.equal(rendered, 3102);
        } else {
            assert.equal(tab.node("history-export").disabled, false);
            assert.equal(tab.downloads.length, 1);
            const events = (await tab.blobs[0].text()).trim().split("\n").map(JSON.parse);
            assert.equal(events.length, 3101);
            assert.equal(events.at(-1).payload.event_id, 3101);
        }
    });
}

test("failed or disconnected history operations release buffers and controls", async () => {
    const tab = await joined();
    const socket = tab.sockets[0];
    socket.receive("auth_ok", { name: "Arxs", reconnect_token: "private-token",
        state: "ACTIVE_TURN", session_id: "session-a", latest_event_id: 1,
        players: [], player_order: [] });
    const replayRequest = socket.sent.at(-1).data;
    socket.receive("error", { code: "journal_request_failed", request: replayRequest, msg: "History failed" });
    assert.equal(tab.session.replaying, false);
    tab.node("history-export").listeners.click();
    const exportRequest = socket.sent.at(-1).data;
    socket.receive("error", { code: "journal_request_failed", request: exportRequest, msg: "History failed" });
    assert.equal(tab.node("history-export").disabled, false);
    tab.node("history-export").listeners.click();
    assert.equal(tab.node("history-export").disabled, true);
    socket.close();
    assert.equal(tab.node("history-export").disabled, false);
    assert.equal(tab.session.exportEvents.length, 0);
});

test("named scenarios round-trip every field across tabs without sending private content", () => {
    const local = storage();
    const tab = browser(local);
    const fields = {
        "scenario-input": "  A Finnish forest.\nHyvää iltaa!  ",
        "guidance-input": "\nThe guide is secretly a ghost.\n",
        "chance-percent": "0", "chance-cadence": "condition",
        "chance-trigger": "A player enters a building", "chance-eligibility": "It is unstable",
        "chance-effect": "The ceiling falls", "chance-scope": "per_player",
    };
    for (const [id, value] of Object.entries(fields)) tab.node(id).value = value;
    const name = "<img src=x onerror=alert(1)>";
    tab.node("scenario-save-name").value = name;
    tab.node("save-scenario").listeners.click();
    const reopened = browser(local);
    assert.equal(reopened.node("saved-scenarios").children[1].textContent, name);
    assert.equal(reopened.node("load-scenario").disabled, true);
    reopened.node("saved-scenarios").value = name;
    reopened.node("saved-scenarios").listeners.change();
    assert.equal(reopened.node("load-scenario").disabled, false);
    reopened.node("load-scenario").listeners.click();
    for (const [id, value] of Object.entries(fields)) assert.equal(reopened.node(id).value, value);
    // Saving a draft does not require a valid chance rule. Loading replaces empty fields too.
    for (const percentage of ["", "100"]) {
        reopened.node("chance-percent").value = percentage;
        reopened.node("chance-eligibility").value = "";
        reopened.node("save-scenario").listeners.click();
        reopened.node("chance-eligibility").value = "stale eligibility";
        reopened.node("load-scenario").listeners.click();
        assert.equal(reopened.node("chance-percent").value, percentage);
        assert.equal(reopened.node("chance-eligibility").value, "");
    }
    assert.equal(local.getItem("artificialDungeonScenarios").includes("secretly a ghost"), true);
    assert.equal(tab.sockets[0].sent.length + reopened.sockets[0].sent.length, 0);
});

test("overwrite and deletion preserve other scenarios, current form, and unrelated storage", () => {
    const local = storage();
    local.setItem("unrelated", "keep me");
    const tab = browser(local);
    for (const name of ["First", "Second"]) {
        tab.node("scenario-save-name").value = name;
        tab.node("scenario-input").value = name;
        tab.node("save-scenario").listeners.click();
    }
    tab.node("scenario-input").value = "Edited second";
    const before = local.getItem("artificialDungeonScenarios");
    tab.runtime.window.confirm = () => false;
    tab.node("save-scenario").listeners.click();
    tab.node("delete-scenario").listeners.click();
    assert.equal(local.getItem("artificialDungeonScenarios"), before);
    tab.runtime.window.confirm = () => true;
    tab.node("save-scenario").listeners.click();
    assert.equal(JSON.parse(local.getItem("artificialDungeonScenarios"))[1].fields.scenario, "Edited second");
    tab.node("delete-scenario").listeners.click();
    assert.deepEqual(JSON.parse(local.getItem("artificialDungeonScenarios")).map((entry) => entry.name), ["First"]);
    assert.equal(tab.node("scenario-input").value, "Edited second");
    assert.equal(local.getItem("unrelated"), "keep me");
    assert.equal(tab.node("delete-scenario").disabled, true);
    tab.node("saved-scenarios").value = "First";
    tab.node("delete-scenario").listeners.click();
    assert.deepEqual(JSON.parse(local.getItem("artificialDungeonScenarios")), []);
    assert.equal(tab.node("saved-scenarios").children[0].textContent, "No saved scenarios");
});

test("scenario storage errors do not erase saves or alter the form", () => {
    for (const invalid of ["{", "null", "{}", '[{"name":"Broken","fields":{}}]']) {
        const local = storage();
        local.setItem("artificialDungeonScenarios", invalid);
        const tab = browser(local);
        tab.node("scenario-input").value = "Keep this draft";
        tab.node("scenario-save-name").value = "Draft";
        tab.node("save-scenario").listeners.click();
        tab.node("saved-scenarios").value = "Broken";
        tab.node("load-scenario").listeners.click();
        tab.node("delete-scenario").listeners.click();
        assert.equal(local.getItem("artificialDungeonScenarios"), invalid);
        assert.equal(tab.node("scenario-input").value, "Keep this draft");
        assert.match(tab.node("scenario-storage-status").textContent, /Could not access/);
    }
    const local = storage();
    const tab = browser(local);
    tab.node("save-scenario").listeners.click();
    assert.match(tab.node("scenario-storage-status").textContent, /Enter a name/);
    tab.node("scenario-save-name").value = "Draft";
    local.setItem = () => { throw new Error("Quota exceeded"); };
    tab.node("save-scenario").listeners.click();
    assert.equal(local.getItem("artificialDungeonScenarios"), null);
    assert.match(tab.node("scenario-storage-status").textContent, /Could not access/);
    local.getItem = () => { throw new Error("Storage blocked"); };
    const blocked = browser(local);
    assert.match(blocked.node("scenario-storage-status").textContent, /Could not access/);
    assert.equal(blocked.sockets.length, 1);
});
