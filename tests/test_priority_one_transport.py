"""Real ASGI WebSocket authorization tests with no model/network calls."""

import asyncio
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api.server import ConnectionManager, create_app
from core.config import settings
from core.schemas import ServerEvent
from logic.llm.errors import LLMResolutionError
from test_engine import FakeResolver, password_digest


def receive_until(socket, kind, predicate=lambda event: True):
    """Receive events until one of the given kind and predicate arrives."""
    for _ in range(30):
        event = socket.receive_json()
        if event["type"] == kind and predicate(event):
            return event
    raise AssertionError(f"Did not receive {kind}")


def authenticate(socket, client_id, name="Host", password=None, reconnect_token=None):
    """Send an auth message for a client."""
    socket.send_json(
        {
            "event_type": "auth",
            "data": {
                "name": name,
                "password_digest": password_digest(
                    password or settings.server.host_password, client_id
                ),
                "reconnect_token": reconnect_token,
            },
        }
    )


def test_unauthenticated_socket_cannot_receive_broadcasts_or_use_existing_identity():
    """Verify an unauthenticated socket cannot receive broadcasts or use an existing identity."""
    app = create_app(FakeResolver)
    host_id, pending_id = str(uuid4()), str(uuid4())
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/{host_id}") as host:
            authenticate(host, host_id)
            receive_until(host, "auth_ok")
            with client.websocket_connect(f"/ws/{pending_id}") as pending:
                host.send_json({"event_type": "chat", "data": {"message": "PRIVATE PARTY CHAT"}})
                receive_until(host, "chat_echo")
                pending.send_json({"event_type": "chat", "data": {"message": "Probe"}})
                # A broadcast queued before the error would be a data leak.
                assert pending.receive_json() == {
                    "type": "error",
                    "payload": {"msg": "Authenticate before sending game messages."},
                }
            with client.websocket_connect(f"/ws/{host_id}") as impostor:
                authenticate(impostor, host_id, password="wrong")
                assert impostor.receive_json()["type"] == "error"
                impostor.send_json({"event_type": "end_game", "data": {}})
                assert impostor.receive_json()["type"] == "error"
                host.send_json(
                    {"event_type": "chat", "data": {"message": "Host still owns socket"}}
                )
                assert (
                    receive_until(host, "chat_echo")["payload"]["chat"] == "Host still owns socket"
                )
                assert app.state.engine.players[host_id].is_connected


def test_password_alone_cannot_reclaim_an_existing_client_id():
    """Verify a password alone cannot reclaim an existing client id."""
    app = create_app(FakeResolver)
    host_id = str(uuid4())
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/{host_id}") as host:
            authenticate(host, host_id)
            token = receive_until(host, "auth_ok")["payload"]["reconnect_token"]
            with client.websocket_connect(f"/ws/{host_id}") as replacement:
                authenticate(replacement, host_id)
                error = replacement.receive_json()
                assert "reconnect token" in error["payload"]["msg"]
                authenticate(replacement, host_id, reconnect_token=token)
                receive_until(replacement, "auth_ok")
                replacement.send_json({"event_type": "chat", "data": {"message": "Reconnected"}})
                receive_until(replacement, "chat_echo")
                assert app.state.engine.players[host_id].is_connected
                assert not app.state.engine.players[host_id].return_pending


def test_new_game_retains_host_and_requires_players_to_join_a_fresh_session():
    """Restart only after ending; replace archives, inference context and player identities."""
    app = create_app(FakeResolver)
    host_id, player_id, pending_id = (str(uuid4()) for _ in range(3))
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/{host_id}") as host:
            authenticate(host, host_id)
            host_token = receive_until(host, "auth_ok")["payload"]["reconnect_token"]
            host.send_json({"event_type": "new_game", "data": {}})
            assert "End the current game" in receive_until(host, "error")["payload"]["msg"]
            host.send_json(
                {
                    "event_type": "scenario_init",
                    "data": {"scenario": "Old gate", "guidance": "Old private guidance"},
                }
            )
            receive_until(host, "scenario_ready")
            with client.websocket_connect(f"/ws/{player_id}") as player:
                authenticate(player, player_id, "Player", settings.server.player_password)
                player_token = receive_until(player, "auth_ok")["payload"]["reconnect_token"]
                host.send_json({"event_type": "start_game", "data": {}})
                receive_until(host, "turn_directive")
                previous = app.state.engine
                host.send_json({"event_type": "end_game", "data": {}})
                receive_until(host, "game_ended")
                receive_until(player, "game_ended")
                player.send_json({"event_type": "new_game", "data": {}})
                assert "Only the host" in receive_until(player, "error")["payload"]["msg"]
                with client.websocket_connect(f"/ws/{pending_id}") as pending:
                    host.send_json({"event_type": "new_game", "data": {}})
                    snapshot = receive_until(host, "auth_ok")["payload"]
                    assert snapshot["state"] == "SCENARIO_INJECTION"
                    assert snapshot["session_id"] != previous.session_id
                    assert snapshot["reconnect_token"] == host_token
                    assert snapshot["latest_event_id"] == 0
                    assert snapshot["scenario_title"] is None
                    assert snapshot["opening_scenario"] is None
                    assert snapshot["accepted_actions"] == []
                    assert list(app.state.engine.players) == [host_id]
                    assert app.state.engine.resolver is not previous.resolver
                    assert app.state.engine.transcript is not previous.transcript
                    assert app.state.engine.journal is not previous.journal
                    assert previous.transcript.path.read_text().endswith("</html>\n")
                    previous_html = previous.transcript.path.read_text()
                    with pytest.raises(WebSocketDisconnect) as exc:
                        player.receive_json()
                    assert exc.value.code == 4002
                    authenticate(pending, pending_id, "Pending", settings.server.player_password)
                    assert "not accepting" in receive_until(pending, "error")["payload"]["msg"]
                    host.send_json(
                        {"event_type": "scenario_init", "data": {"scenario": "New forest"}}
                    )
                    receive_until(host, "scenario_ready")
                    # A socket opened in the old session authenticates against the new engine.
                    authenticate(pending, pending_id, "Pending", settings.server.player_password)
                    assert receive_until(pending, "auth_ok")["payload"]["session_id"] == (
                        snapshot["session_id"]
                    )
                    with client.websocket_connect(f"/ws/{player_id}") as rejoined:
                        authenticate(
                            rejoined,
                            player_id,
                            "Player",
                            settings.server.player_password,
                            reconnect_token=player_token,
                        )
                        fresh = receive_until(rejoined, "auth_ok")["payload"]
                        assert fresh["reconnect_token"] != player_token
                        assert fresh["session_id"] == snapshot["session_id"]
                        host.send_json({"event_type": "start_game", "data": {}})
                        receive_until(host, "turn_directive")
                        assert app.state.engine.resolver.scenario == "New forest"
                        assert app.state.engine.resolver.start_names == [
                            ["Host", "Pending", "Player"]
                        ]
                        new_path = app.state.engine.transcript.path
                        assert new_path != previous.transcript.path
                        assert "New forest" in new_path.read_text()
                        assert "Old gate" not in new_path.read_text()
                        assert "Old private guidance" not in new_path.read_text()
                        assert previous.transcript.path.read_text() == previous_html


def test_old_action_failure_cannot_reply_to_a_player_in_the_new_game(monkeypatch):
    """A socket's pending preflight must lose reply rights when its session is replaced."""

    class DelayedResolver(FakeResolver):
        def __init__(self):
            super().__init__()
            self.block = False
            self.entered = asyncio.Event()
            self.release = asyncio.Event()
            self.closed = False

        async def preflight_round(self, actions, current_state=""):
            if self.block:
                self.entered.set()
                await self.release.wait()
                raise LLMResolutionError("Old game preflight failed")

        async def close(self):
            self.closed = True

    app = create_app(DelayedResolver)
    host_id, player_id = str(uuid4()), str(uuid4())
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/{host_id}") as host:
            authenticate(host, host_id)
            receive_until(host, "auth_ok")
            host.send_json({"event_type": "scenario_init", "data": {"scenario": "Old gate"}})
            receive_until(host, "scenario_ready")
            with client.websocket_connect(f"/ws/{player_id}") as player:
                authenticate(player, player_id, "Player", settings.server.player_password)
                receive_until(player, "auth_ok")
                host.send_json({"event_type": "start_game", "data": {}})
                receive_until(host, "turn_directive")
                previous = app.state.engine
                host.send_json({"event_type": "action", "data": {"action": "Open gate"}})
                receive_until(
                    host, "turn_directive", lambda e: e["payload"]["active_player_id"] == player_id
                )
                previous.resolver.block = True
                finished = asyncio.Event()
                process = previous.process_payload

                async def observed_payload(client_id, payload, **kwargs):
                    try:
                        await process(client_id, payload, **kwargs)
                    finally:
                        if client_id == player_id and payload.event_type == "action":
                            finished.set()

                monkeypatch.setattr(previous, "process_payload", observed_payload)
                player.send_json({"event_type": "action", "data": {"action": "Watch gate"}})

                async def wait_for_preflight():
                    await asyncio.wait_for(previous.resolver.entered.wait(), 2)

                client.portal.call(wait_for_preflight)
                host.send_json({"event_type": "end_game", "data": {}})
                receive_until(host, "game_ended")
                host.send_json({"event_type": "new_game", "data": {}})
                receive_until(host, "auth_ok")
                assert previous.resolver.closed
                host.send_json({"event_type": "scenario_init", "data": {"scenario": "New forest"}})
                receive_until(host, "scenario_ready")
                with client.websocket_connect(f"/ws/{player_id}") as rejoined:
                    authenticate(rejoined, player_id, "Player", settings.server.player_password)
                    receive_until(rejoined, "auth_ok")

                    async def finish_old_preflight():
                        previous.resolver.release.set()
                        await asyncio.wait_for(finished.wait(), 2)

                    client.portal.call(finish_old_preflight)
                    rejoined.send_json(
                        {"event_type": "chat", "data": {"message": "New game message"}}
                    )
                    # The chat is a FIFO barrier after any late preflight error.
                    for _ in range(30):
                        event = rejoined.receive_json()
                        assert event["type"] != "error"
                        if event["type"] == "chat_echo":
                            assert event["payload"]["chat"] == "New game message"
                            break
                    else:
                        raise AssertionError("New session chat was not delivered")


def test_invalid_auth_attempt_limit_includes_malformed_json():
    """Verify the invalid auth attempt limit includes malformed JSON."""
    settings.server.max_auth_attempts = 2
    with TestClient(create_app(FakeResolver)) as client:
        with client.websocket_connect(f"/ws/{uuid4()}") as socket:
            socket.send_text("{")
            assert socket.receive_json()["type"] == "error"
            socket.send_text("{")
            assert socket.receive_json()["type"] == "error"
            with pytest.raises(WebSocketDisconnect) as exc:
                socket.receive_json()
            assert exc.value.code == 1008


def test_pending_connection_cap_and_deadline():
    """Verify the pending connection cap and deadline."""
    settings.server.max_pending_connections = 1
    settings.server.auth_timeout_seconds = 0.1
    app = create_app(FakeResolver)
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/{uuid4()}") as idle:
            with pytest.raises(WebSocketDisconnect):
                with client.websocket_connect(f"/ws/{uuid4()}"):
                    pass
            with pytest.raises(WebSocketDisconnect) as exc:
                idle.receive_json()
            assert exc.value.code == 1008


@pytest.mark.parametrize("host,player", [(None, "player"), ("host", None), ("same", "same")])
def test_direct_asgi_startup_rejects_missing_or_equal_passwords(host, player):
    """Verify direct ASGI startup rejects missing or equal passwords."""
    settings.server.host_password = host
    settings.server.player_password = player
    with pytest.raises(ValueError):
        with TestClient(create_app(FakeResolver)):
            pass


def test_manager_pending_promotion_and_old_disconnect_do_not_affect_replacement():
    """Verify pending promotion and old disconnect do not affect a replacement."""

    class Socket:
        """Minimal fake WebSocket for the manager."""

        def __init__(self):
            """Initialize the fake socket."""
            self.messages = []
            self.closed = False

        async def accept(self):
            """Accept the socket."""
            pass

        async def send_text(self, text):
            """Record a sent text."""
            self.messages.append(text)

        async def close(self, code=1000):
            """Mark the socket closed."""
            self.closed = True

    async def run():
        manager = ConnectionManager()
        old, new = Socket(), Socket()
        await manager.connect("one", old)
        manager.promote("one", old)
        await manager.connect("one", new)
        await manager.broadcast_global(ServerEvent(type="chat_echo", payload={"chat": "hello"}))
        assert len(old.messages) == 1 and new.messages == [] and not old.closed
        assert manager.promote("one", new) is old
        assert not manager.owns("one", old)
        assert not await manager.disconnect("one", old)
        assert manager.owns("one", new)
        await manager.close()
        assert new.closed and not manager.pending

    asyncio.run(run())


@pytest.mark.parametrize("rejoin_host", [False, True])
def test_active_game_reconnect_restores_original_player_with_private_proof(rejoin_host):
    """Reopened tabs must reuse the saved ID/token; shared credentials alone cannot take over."""
    app = create_app(FakeResolver)
    host_id, player_id = str(uuid4()), str(uuid4())
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/{host_id}") as host:
            authenticate(host, host_id)
            host_token = receive_until(host, "auth_ok")["payload"]["reconnect_token"]
            host.send_json({"event_type": "scenario_init", "data": {"scenario": "A cabin."}})
            receive_until(host, "scenario_ready")
            with client.websocket_connect(f"/ws/{player_id}") as player:
                authenticate(player, player_id, "Arxs", settings.server.player_password)
                player_token = receive_until(player, "auth_ok")["payload"]["reconnect_token"]
                host.send_json({"event_type": "start_game", "data": {}})
                receive_until(player, "turn_directive")
                if rejoin_host:
                    original, observer = host, player
                    identity, name, token = host_id, "Host", host_token
                    password = settings.server.host_password
                else:
                    original, observer = player, host
                    identity, name, token = player_id, "Arxs", player_token
                    password = settings.server.player_password
                original.close()
                receive_until(
                    observer,
                    "system_msg",
                    lambda event: (event["payload"]["msg"] == f"{name} disconnected."),
                )
                newcomer_id = str(uuid4())
                with client.websocket_connect(f"/ws/{newcomer_id}") as newcomer:
                    authenticate(newcomer, newcomer_id, name, password)
                    assert "not accepting new players" in newcomer.receive_json()["payload"]["msg"]
                with client.websocket_connect(f"/ws/{identity}") as recovered:
                    authenticate(recovered, identity, name, password, "incorrect-token")
                    assert "reconnect token" in recovered.receive_json()["payload"]["msg"]
                    authenticate(recovered, identity, name, password, token)
                    snapshot = receive_until(recovered, "auth_ok")["payload"]
                    assert snapshot["client_id"] == identity
                    assert snapshot["name"] == name
                    assert snapshot["is_host"] is rejoin_host
                    assert snapshot["round_number"] == 1
                    assert len(app.state.engine.players) == 2
                    assert app.state.engine.players[identity].is_connected
                    recovered.send_json({"event_type": "chat", "data": {"message": "I'm back"}})
                    assert receive_until(recovered, "chat_echo")["payload"]["name"] == name


@pytest.mark.parametrize("mode", ["replay", "export"])
def test_long_history_has_independent_budget_and_retriable_page(monkeypatch, mode):
    from api import admission

    now = [100.0]
    monkeypatch.setattr(admission, "monotonic", lambda: now[0])
    app = create_app(FakeResolver)
    host_id = str(uuid4())
    with TestClient(app) as client:
        with client.websocket_connect(f"/ws/{host_id}") as host:
            authenticate(host, host_id)
            receive_until(host, "auth_ok")
            for number in range(30):
                host.send_json({"event_type": "chat", "data": {"message": f"Chat {number}"}})
                receive_until(host, "chat_echo")
            journal = app.state.engine.journal
            # Seed the remaining archive in one batch; all gateway traffic still
            # travels through the real ASGI socket and admission policy.
            events = tuple(
                ServerEvent(
                    type="chat_echo",
                    payload={
                        "chat": f"Archived {number}",
                        "session_id": journal.session_id,
                        "event_id": number,
                    },
                )
                for number in range(journal.cursor + 1, 3102)
            )
            journal._append_batch(events)
            journal.cursor = 3101
            cursor = 0
            received = []
            for _ in range(30):
                data = {"mode": mode, "after": cursor, "limit": 100, "search": ""}
                host.send_json({"event_type": "journal_request", "data": data})
                page = receive_until(host, "journal_page")["payload"]
                assert not page["incomplete"]
                received.extend(page["events"])
                cursor = page["cursor"]
            assert cursor == 3000
            data["after"] = cursor
            host.send_json({"event_type": "journal_request", "data": data})
            error = receive_until(host, "error")["payload"]
            assert error["code"] == "journal_rate_limited"
            assert error["request"] == data and error["retry_after_seconds"] == 10.0
            now[0] += error["retry_after_seconds"] + 0.1
            while True:
                data["after"] = cursor
                host.send_json({"event_type": "journal_request", "data": data})
                page = receive_until(host, "journal_page")["payload"]
                received.extend(page["events"])
                cursor = page["cursor"]
                if not page["has_more"]:
                    break
            assert cursor == 3101
            assert [event["payload"]["event_id"] for event in received] == list(range(1, 3102))
            host.send_json({"event_type": "chat", "data": {"message": "Still connected"}})
            assert receive_until(host, "chat_echo")["payload"]["chat"] == "Still connected"
