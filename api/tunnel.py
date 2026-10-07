"""Announce a connected Quick Tunnel through its private cloudflared metrics API."""

import asyncio
import logging
import re

import httpx

LOGGER = logging.getLogger(__name__)


async def announce_quick_tunnel(metrics_url: str) -> None:
    """Wait briefly without blocking game startup; no tunnel is also a valid setup."""
    try:
        async with asyncio.timeout(120), httpx.AsyncClient(timeout=2, trust_env=False) as client:
            while True:
                try:
                    ready = await client.get(metrics_url + "/ready")
                    ready.raise_for_status()
                    response = await client.get(metrics_url + "/quicktunnel")
                    response.raise_for_status()
                    hostname = response.json()["hostname"]
                    if isinstance(hostname, str) and re.fullmatch(
                        r"[a-z0-9-]+\.trycloudflare\.com", hostname
                    ):
                        LOGGER.info(
                            "\n========== Anyworld tunnel connected ==========\n"
                            "Share this address with players:\nhttps://%s\n"
                            "==============================================",
                            hostname,
                        )
                        return
                except (httpx.HTTPError, ValueError, KeyError, TypeError):
                    pass
                await asyncio.sleep(2)
    except TimeoutError:
        # Direct-only deployments have no tunnel service; leave their startup quiet.
        return
