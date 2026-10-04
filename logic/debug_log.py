"""Opt-in private diagnostics, captured before SDK parsing or narrative validation."""

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
import json
import logging
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

import httpx

LOGGER = logging.getLogger(__name__)
MAX_DEBUG_LINE_LENGTH = 150
_REQUEST_TYPE: ContextVar[str] = ContextVar("anyworld_debug_request_type", default="unknown")
_ROUND_NUMBER: ContextVar[int | None] = ContextVar("anyworld_debug_round_number", default=None)

# Title and opening-state requests happen outside a round.  These are the calls
# that should be combined into one round diagnostic after a successful round.
_ROUND_REQUEST_TYPES = frozenset(
    {
        "chance_rule",
        "chance_trigger",
        "dice",
        "dice_audit",
        "event_audit",
        "round",
        "summary",
        "summary_audit",
    }
)


@contextmanager
def request_type_context(kind: str):
    """Make the current inference request type available to HTTP debug hooks."""
    token = _REQUEST_TYPE.set(kind)
    try:
        yield
    finally:
        _REQUEST_TYPE.reset(token)


def set_debug_round_number(number: int | None) -> None:
    """Associate subsequent provider calls in this task with one game round."""
    _ROUND_NUMBER.set(number)


def _safe_request_type(value: object) -> str:
    """Return a filename-safe request type without exposing arbitrary text."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", str(value)) or "unknown"


def _decode_display_escapes(value: str) -> str:
    """Decode JSON escape sequences without introducing surrogate characters."""
    replacements = {
        '"': '"',
        "\\": "\\",
        "/": "/",
        "b": "\b",
        "f": "\f",
        "n": "\n",
        "r": "\r",
        "t": "\t",
    }
    decoded: list[str] = []
    index = 0
    while index < len(value):
        if value[index] != "\\" or index + 1 >= len(value):
            decoded.append(value[index])
            index += 1
            continue
        escape = value[index + 1]
        if escape == "u" and index + 5 < len(value):
            code = value[index + 2 : index + 6]
            if re.fullmatch(r"[0-9a-fA-F]{4}", code):
                codepoint = int(code, 16)
                index += 6
                if (
                    0xD800 <= codepoint <= 0xDBFF
                    and value[index : index + 2] == "\\u"
                    and re.fullmatch(r"[0-9a-fA-F]{4}", value[index + 2 : index + 6])
                ):
                    low = int(value[index + 2 : index + 6], 16)
                    if 0xDC00 <= low <= 0xDFFF:
                        codepoint = 0x10000 + ((codepoint - 0xD800) << 10) + (low - 0xDC00)
                        index += 6
                    else:
                        codepoint = 0xFFFD
                elif 0xD800 <= codepoint <= 0xDFFF:
                    codepoint = 0xFFFD
                decoded.append(chr(codepoint))
                continue
        replacement = replacements.get(escape)
        if replacement is None:
            decoded.append("\\")
            index += 1
        else:
            decoded.append(replacement)
            index += 2
    return "".join(decoded)


def _format_string(value: str, indent: int) -> str:
    """Render text directly, decoding leftover JSON escapes for display."""

    rendered = value
    for _ in range(3):
        decoded = _decode_display_escapes(rendered)
        if decoded == rendered:
            break
        rendered = decoded
    rendered = rendered.replace("\r\n", "\n").replace("\r", "\n")
    return rendered.replace("\n", "\n" + " " * indent)


def _format_json_value(value: Any, indent: int = 0) -> str:
    """Pretty-print JSON-like data, including nested JSON response strings."""
    if isinstance(value, str):
        stripped = value.lstrip()
        if stripped.startswith(("{", "[")):
            try:
                return _format_json_value(json.loads(value), indent)
            except json.JSONDecodeError:
                pass
        return _format_string(value, indent)
    if value is None or isinstance(value, (bool, int, float)):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        if not value:
            return "[]"
        child_indent = indent + 2
        lines = ["["]
        for item in value:
            rendered_lines = _format_json_value(item, child_indent).split("\n")
            lines.append(f"{' ' * child_indent}{rendered_lines[0]}")
            lines.extend(rendered_lines[1:])
        lines.append(" " * indent + "]")
        return "\n".join(lines)
    if isinstance(value, dict):
        if not value:
            return "{}"
        child_indent = indent + 2
        lines = ["{"]
        for key, item in value.items():
            rendered_lines = _format_json_value(item, child_indent).split("\n")
            lines.append(f"{' ' * child_indent}{str(key)}: {rendered_lines[0]}")
            lines.extend(rendered_lines[1:])
        lines.append(" " * indent + "}")
        return "\n".join(lines)
    return _format_string(str(value), indent)


def _wrap_long_lines(text: str, width: int = MAX_DEBUG_LINE_LENGTH) -> str:
    """Hard-wrap diagnostic lines while preserving all existing newlines."""
    wrapped: list[str] = []
    for line in text.split("\n"):
        if not line:
            wrapped.append(line)
            continue
        wrapped.extend(line[index : index + width] for index in range(0, len(line), width))
    return "\n".join(wrapped)


def format_debug_body(body: str | None) -> str:
    """Render a JSON body readably, unescaping nested response content."""
    if body is None:
        return "(not received)"
    try:
        payload = json.loads(body)
    except (TypeError, json.JSONDecodeError):
        return _wrap_long_lines(_format_string(body, 0))
    return _wrap_long_lines(_format_json_value(payload))


def _round_record_text(record: dict[str, Any]) -> str:
    """Render one request/response record as a readable text section."""
    request = record.get("request") or {}
    status = record.get("status_code")
    status_text = "pending (no response captured)" if status is None else str(status)
    lines = [
        f"Request type: {record.get('request_type', 'unknown')}",
        f"Timestamp: {record.get('timestamp', 'unknown')}",
        f"HTTP status: {status_text}",
        "",
        "REQUEST",
        "-------",
        f"{request.get('method', 'UNKNOWN')} {request.get('url', '')}",
        format_debug_body(request.get("body")),
        "",
        "RESPONSE",
        "--------",
        format_debug_body(record.get("body")),
    ]
    thinking = record.get("thinking_sequences") or []
    if thinking:
        lines.extend(
            ["", "THINKING FIELDS", "---------------", format_debug_body(json.dumps(thinking))]
        )
    return _wrap_long_lines("\n".join(lines).rstrip()) + "\n"


class RawResponseLogger:
    """Save completion diagnostics without making provider failures fatal."""

    def __init__(self, directory: Path = Path(".debug/llm")) -> None:
        """Select a private directory outside the served static tree."""
        self.directory = directory
        self._round_temp_files: dict[int, set[str]] = {}

    @staticmethod
    def _round_filename(round_number: int, timestamp: datetime, request_type: str) -> str:
        """Build a sortable temporary filename for one round request."""
        return (
            f"{timestamp:%Y%m%dT%H%M%SZ}-round-{round_number:04d}-"
            f"{request_type}-{uuid4().hex}.tmp"
        )

    @staticmethod
    def _standalone_filename(timestamp: datetime, request_type: str) -> str:
        """Build a standalone filename for non-round diagnostics."""
        return f"{timestamp:%Y%m%dT%H%M%SZ}-{request_type}-{uuid4().hex}.log"

    async def capture_request(self, request: httpx.Request) -> None:
        """Persist the sent completion body before waiting for a response."""
        if not request.url.path.endswith("/chat/completions"):
            return
        try:
            body = request.content
        except (httpx.RequestNotRead, RuntimeError):
            body = b""
        timestamp = datetime.now(timezone.utc)
        request_type = _safe_request_type(_REQUEST_TYPE.get())
        round_number = _ROUND_NUMBER.get()
        filename = self._standalone_filename(timestamp, request_type)
        request_record = {
            "method": request.method,
            "url": request.url.path,
            "body": body.decode("utf-8", errors="replace"),
        }
        record = {
            "timestamp": timestamp.isoformat(),
            "request_type": request_type,
            "round_number": round_number,
            "status_code": None,
            "request": request_record,
            "body": None,
            "thinking_sequences": [],
        }
        is_round_request = round_number is not None and request_type in _ROUND_REQUEST_TYPES
        if is_round_request:
            filename = self._round_filename(round_number, timestamp, request_type)
            self._round_temp_files.setdefault(round_number, set()).add(filename)
        else:
            record.pop("round_number")
        write = asyncio.create_task(
            asyncio.to_thread(self._write, filename, record)
        )
        request.extensions["anyworld_debug_request"] = request_record
        request.extensions["anyworld_debug_request_type"] = request_type
        request.extensions["anyworld_debug_round_number"] = round_number
        request.extensions["anyworld_debug_is_round"] = is_round_request
        request.extensions["anyworld_debug_filename"] = filename
        request.extensions["anyworld_debug_write"] = write
        try:
            await asyncio.shield(write)
        except asyncio.CancelledError:
            await write
            raise

    async def capture(self, response: httpx.Response) -> None:
        """Record even malformed/rejected response bodies without changing SDK input."""
        if not response.request.url.path.endswith("/chat/completions"):
            return
        body = await response.aread()
        write = response.request.extensions.get("anyworld_debug_write")
        if isinstance(write, asyncio.Task):
            await asyncio.shield(write)
        filename = response.request.extensions.get("anyworld_debug_filename")
        timestamp = datetime.now(timezone.utc)
        request_type = response.request.extensions.get("anyworld_debug_request_type", "unknown")
        round_number = response.request.extensions.get("anyworld_debug_round_number")
        is_round_request = response.request.extensions.get("anyworld_debug_is_round", False)
        record = {
            "timestamp": timestamp.isoformat(),
            "request_type": request_type,
            "round_number": round_number,
            "status_code": response.status_code,
            "request": response.request.extensions.get("anyworld_debug_request"),
            "body": body.decode("utf-8", errors="replace"),
            "thinking_sequences": self._thinking_sequences(body),
        }
        if not isinstance(filename, str):
            request_type = _safe_request_type(record["request_type"])
            record["request_type"] = request_type
            is_round_request = (
                isinstance(round_number, int) and request_type in _ROUND_REQUEST_TYPES
            )
            filename = (
                self._round_filename(round_number, timestamp, request_type)
                if is_round_request
                else self._standalone_filename(timestamp, request_type)
            )
            if is_round_request:
                self._round_temp_files.setdefault(round_number, set()).add(filename)
        if not is_round_request:
            record.pop("round_number")
        write = asyncio.create_task(
            asyncio.to_thread(self._write, filename, record)
        )
        try:
            await asyncio.shield(write)
        except asyncio.CancelledError:
            await write
            raise

    @staticmethod
    def _thinking_sequences(body: bytes) -> list[dict[str, object]]:
        """Extract provider reasoning fields while retaining the complete raw body."""
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return []
        if not isinstance(payload, dict):
            return []
        sequences: list[dict[str, object]] = []
        choices = payload.get("choices")
        if not isinstance(choices, list):
            return sequences
        for index, choice in enumerate(choices):
            if not isinstance(choice, dict):
                continue
            message = choice.get("message")
            delta = choice.get("delta")
            source = message if isinstance(message, dict) else delta
            if not isinstance(source, dict):
                continue
            thinking = source.get("reasoning_content")
            if thinking is None:
                thinking = source.get("thinking")
            if isinstance(thinking, str) and thinking:
                sequences.append({"choice_index": index, "content": thinking})
        return sequences

    def _write(self, filename: str, record: dict[str, Any]) -> None:
        """Keep diagnostic disk failures nonfatal and private data out of console logs."""
        self._write_text(filename, _round_record_text(record))

    def _write_text(self, filename: str, text: str) -> bool:
        """Write a diagnostic and report failure without leaking its contents."""
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            (self.directory / filename).write_text(text, encoding="utf-8")
            return True
        except OSError as exc:
            LOGGER.warning("Raw response debug log could not be written: %s", type(exc).__name__)
            return False

    async def finish_round(self, round_number: int, summary: dict[str, Any]) -> None:
        """Combine a completed round's temporary records and remove those records."""
        await asyncio.to_thread(self._finish_round, round_number, summary)

    def _finish_round(self, round_number: int, summary: dict[str, Any]) -> None:
        """Combine round records without blocking the event loop."""
        files = sorted(
            self.directory / filename
            for filename in self._round_temp_files.get(round_number, set())
        )
        if not files:
            return
        sections = [
            "=" * 78,
            f"ROUND {round_number} DEBUG LOG",
            "=" * 78,
            "",
            "FULL ROUND SUMMARY",
            "------------------",
            _format_json_value(summary),
            "",
        ]
        for index, path in enumerate(files, start=1):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                LOGGER.warning("Round debug record could not be read: %s", type(exc).__name__)
                continue
            sections.extend(
                [
                    f"REQUEST {index}",
                    "=" * 40,
                    text.rstrip(),
                    "",
                ]
            )
        timestamp = datetime.now(timezone.utc)
        final_stem = f"{timestamp:%Y%m%dT%H%M%SZ}-round-{round_number:04d}-debug"
        final_path = self.directory / f"{final_stem}.log"
        suffix = 2
        while final_path.exists():
            final_path = self.directory / f"{final_stem}-{suffix}.log"
            suffix += 1
        final_text = _wrap_long_lines("\n".join(sections).rstrip()) + "\n"
        written = self._write_text(final_path.name, final_text)
        if not written:
            return
        for path in files:
            try:
                path.unlink()
            except OSError as exc:
                LOGGER.warning(
                    "Round debug temporary file could not be removed: %s", type(exc).__name__
                )
        self._round_temp_files.pop(round_number, None)
