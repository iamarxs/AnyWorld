"""Real ASGI WebSocket authorization tests with no model/network calls."""

import asyncio
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api.server import ConnectionManager, create_app
from core.config import settings
from core.schemas import ServerEvent
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
