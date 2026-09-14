"""HTTP asset-serving smoke tests."""

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from api.server import app


def test_index_and_static_assets_are_served() -> None:
    """Serve the index page and static assets."""
    with TestClient(app) as client:
        index = client.get("/")
        script = client.get("/static/js/app.js")
        stylesheet = client.get("/static/css/style.css")

    assert index.status_code == 200
    assert "login-modal" in index.text
    assert script.status_code == 200
    assert "WebSocket" in script.text
    assert stylesheet.status_code == 200
    assert "grid-template-areas" in stylesheet.text


def test_websocket_rejects_malformed_client_id() -> None:
    """Reject a non-UUID client id on the WebSocket."""
    with TestClient(app) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/ws/not-a-uuid"):
                pass

    assert exc_info.value.code == 1008
