"""HTTP asset-serving smoke tests."""

from fastapi.testclient import TestClient

from api.server import app


def test_index_and_static_assets_are_served() -> None:
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
