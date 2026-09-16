const assert = require("node:assert/strict");
const { test } = require("node:test");
const { readFileSync } = require("node:fs");
const { randomUUID, createHash } = require("node:crypto");
const vm = require("node:vm");
const path = require("node:path");

const source = readFileSync(path.join(__dirname, "../static/js/app.js"), "utf8");

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
    function node(id) {
        if (!nodes.has(id)) nodes.set(id, {
            hidden: id === "grid-container", disabled: false, value: "", textContent: "",
            dataset: {}, style: {}, children: [], listeners: {},
            classList: { add() {}, remove() {} },
            addEventListener(type, callback) { this.listeners[type] = callback; },
            querySelector() { return node("button"); },
            querySelectorAll() { return []; },
            append() {}, appendChild() {}, replaceChildren() {}, focus() {},
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
        close() { this.readyState = 3; this.listeners.close?.(); }
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
        crypto: { randomUUID }, localStorage, sessionStorage, setTimeout,
    };
    const runtime = {
        window, sessionStorage, WebSocket: Socket, setTimeout, clearTimeout, console,
        document: { getElementById: node, createElement: node },
    };
    vm.runInNewContext(source, runtime);
    return {
        runtime,
        sockets, node, localStorage, sessionStorage, hash: runtime.fallbackSha256,
        async login(name = "Arxs", password = "party-password") {
            node("name-input").value = name;
            node("password-input").value = password;
            await node("login-form").listeners.submit({ preventDefault() {} });
        },
        accept(socket = sockets.at(-1), name = "Arxs", token = "private-token") {
            socket.receive("auth_ok", {
                name, reconnect_token: token, is_host: false, state: "ACTIVE_TURN",
                players: [], player_order: [],
            });
        },
        reconnect() {
            const timer = [...timers.values()].find(({ delay }) => delay === 1000);
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

for (const value of ["", "abc", "x".repeat(55), "x".repeat(56), "x".repeat(64),
    "x".repeat(200), "Salasana 🌲 äö漢字"]) {
    test(`fallback SHA-256 matches standard hashing for ${value.length} characters`, () => {
        assert.equal(browser().hash(value), createHash("sha256").update(value).digest("hex"));
    });
}

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
