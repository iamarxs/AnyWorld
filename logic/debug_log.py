"""Opt-in private diagnostics, captured before SDK parsing or narrative validation."""

import asyncio
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
from uuid import uuid4

import httpx

LOGGER = logging.getLogger(__name__)


class RawResponseLogger:
    """Save completion response bodies only; never request headers or credentials."""

    def __init__(self, directory: Path = Path(".debug/llm")) -> None:
        """Select a private directory outside the served static tree."""
        self.directory = directory

    async def capture(self, response: httpx.Response) -> None:
        """Record even malformed/rejected response bodies without changing SDK input."""
        if not response.request.url.path.endswith("/chat/completions"):
            return
        body = await response.aread()
        timestamp = datetime.now(timezone.utc)
        record = {
            "timestamp": timestamp.isoformat(),
            "status_code": response.status_code,
            "body": body.decode("utf-8", errors="replace"),
        }
        filename = f"{timestamp:%Y%m%dT%H%M%S.%fZ}-{uuid4().hex}.json"
        write = asyncio.create_task(asyncio.to_thread(self._write, filename, record))
        try:
            await asyncio.shield(write)
        except asyncio.CancelledError:
            await write
            raise

    def _write(self, filename: str, record: dict) -> None:
        """Keep disk failures nonfatal and private data out of console logs."""
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            (self.directory / filename).write_text(
                json.dumps(record, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
        except OSError as exc:
            LOGGER.warning("Raw response debug log could not be written: %s", type(exc).__name__)
