"""Offline tunnel announcements and console logger presentation."""

import asyncio
from io import StringIO
import logging

import httpx
import pytest

from api import server, tunnel
from core.console_logging import ModuleFormatter
from support import FakeResolver


@pytest.mark.parametrize(
    "hostname",
    [
        "adventure.trycloudflare.com",
        "bad.example\nmisleading log",
        "adventure.trycloudflare.com.evil.example",
        "evil.example/trycloudflare.com",
        "evil.example@adventure.trycloudflare.com",
        "nottrycloudflare.com",
    ],
)
def test_tunnel_banner_requires_ready_connection_and_valid_hostname(monkeypatch, caplog, hostname):
    requests = []
    attempted = asyncio.Event()
    client_class = httpx.AsyncClient

    def respond(request):
        requests.append(request.url.path)
        if len(requests) == 1:
            attempted.set()
            return httpx.Response(503)
        if request.url.path == "/ready":
            return httpx.Response(200)
        return httpx.Response(200, json={"hostname": hostname})

    monkeypatch.setattr(
        tunnel.httpx,
        "AsyncClient",
        lambda **kwargs: client_class(transport=httpx.MockTransport(respond), **kwargs),
    )
    caplog.set_level(logging.INFO, logger="api.tunnel")

    async def run():
        task = asyncio.create_task(tunnel.announce_quick_tunnel("http://tunnel:20241"))
        await attempted.wait()
        assert "Share this address" not in caplog.text
        if hostname == "adventure.trycloudflare.com":
            await asyncio.wait_for(task, 5)
        else:
            await asyncio.sleep(2.1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(run())
    assert requests[:3] == ["/ready", "/ready", "/quicktunnel"]
    assert ("Share this address with players:" in caplog.text) is (
        hostname == "adventure.trycloudflare.com"
    )
    if hostname == "adventure.trycloudflare.com":
        assert "https://adventure.trycloudflare.com" in caplog.records[-1].getMessage().splitlines()


def test_game_shutdown_cancels_optional_tunnel_watcher(monkeypatch):
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def watcher(url):
        assert url == "http://tunnel:20241"
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    monkeypatch.setenv("ANYWORLD_TUNNEL_METRICS", "http://tunnel:20241/")
    monkeypatch.setattr(server, "announce_quick_tunnel", watcher)

    async def run():
        app = server.create_app(FakeResolver)
        async with app.router.lifespan_context(app):
            await asyncio.wait_for(started.wait(), 1)
        assert cancelled.is_set()

    asyncio.run(run())


@pytest.mark.parametrize("level", [logging.INFO, logging.WARNING, logging.ERROR])
def test_uvicorn_display_alias_preserves_severity_and_original_record(level):
    formatter = ModuleFormatter(StringIO())
    record = logging.LogRecord("uvicorn.error", level, __file__, 1, "connection open", (), None)
    rendered = formatter.format(record)
    assert f"{logging.getLevelName(level)} uvicorn.server: connection open" in rendered
    assert record.name == "uvicorn.error" and record.levelno == level
