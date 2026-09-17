"""Console-only module colors, with plain output for unsupported streams."""

import logging
import os
import re
import sys
from typing import TextIO
from threading import Lock

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"
# Muted colors readable on black; reserve distinct entries for known loggers.
_PALETTE = (110, 108, 180, 146, 109, 138, 145, 173, 150, 176, 179, 116)
# All base colors use cube coordinates 1..4. Adding 43 raises each RGB channel
# one step (40/255), preserving hue without clipping or relying on terminal bold.
_BRIGHTER = 43
_MODULES = (
    "app",
    "api.tls_bootstrap",
    "api.server",
    "logic.engine",
    "logic.lobby",
    "logic.llm_manager",
    "logic.debug_log",
    "logic.transcript",
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
    "httpx",
)
# Additional muted 256-color entries, excluding all reserved colors. Never wrap
# the palette: a terminal with no unused colors left falls back to plain text.
_EXTRA_COLORS = tuple(
    16 + 36 * red + 6 * green + blue
    for red in range(1, 5)
    for green in range(1, 5)
    for blue in range(1, 5)
    if max(red, green, blue) - min(red, green, blue) <= 2
    and red + green + blue >= 8
    and 16 + 36 * red + 6 * green + blue not in _PALETTE
)
_RESPONSE_STATUS = re.compile(r'(\d{3})(?:\s+\w+)?\s*"?\s*$')


def _module_palette() -> tuple[int, ...]:
    """Reserve both shades so a warning cannot borrow another module's color."""
    palette = list(_PALETTE)
    used = set(palette) | {color + _BRIGHTER for color in palette}
    for color in _EXTRA_COLORS:
        if color not in used and color + _BRIGHTER not in used:
            palette.append(color)
            used.update((color, color + _BRIGHTER))
    return tuple(palette)


def _windows_vt_enabled(stream: TextIO) -> bool:
    """Enable ANSI processing only if this stream is a Windows console."""
    import ctypes
    import msvcrt
    from ctypes import wintypes

    try:
        handle = wintypes.HANDLE(msvcrt.get_osfhandle(stream.fileno()))
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        get_mode = kernel.GetConsoleMode
        get_mode.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
        get_mode.restype = wintypes.BOOL
        set_mode = kernel.SetConsoleMode
        set_mode.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        set_mode.restype = wintypes.BOOL
        mode = wintypes.DWORD()
        return bool(get_mode(handle, ctypes.byref(mode)) and set_mode(handle, mode.value | 4))
    except (AttributeError, OSError, ValueError):
        return False


def _color_depth(stream: TextIO) -> int:
    """Honor terminal capabilities and NO_COLOR; never color redirected logs."""
    if "NO_COLOR" in os.environ or os.environ.get("TERM") == "dumb":
        return 0
    try:
        if not stream.isatty():
            return 0
    except (AttributeError, OSError, ValueError):
        return 0
    if os.name == "nt":
        return 256 if _windows_vt_enabled(stream) else 0
    term = os.environ.get("TERM", "")
    if "256color" in term or os.environ.get("COLORTERM") in {"truecolor", "24bit"}:
        return 256
    return 256 if term.startswith(("xterm", "screen", "tmux", "vt", "linux", "ansi")) else 0


class ModuleFormatter(logging.Formatter):
    """Color the rendered text without putting escape codes into shared records."""

    def __init__(self, stream: TextIO) -> None:
        super().__init__(LOG_FORMAT, datefmt="%H:%M:%S")
        self.color_depth = _color_depth(stream)
        palette = _module_palette()
        self._colors = dict(zip(_MODULES, palette))
        self._unused_colors = iter(palette[len(_MODULES) :])
        self._color_lock = Lock()

    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        if not self.color_depth:
            return rendered
        with self._color_lock:
            if record.name not in self._colors:
                self._colors[record.name] = next(self._unused_colors, None)
            color = self._colors[record.name]
        if color is None:
            return rendered
        if record.levelno >= logging.WARNING:
            color += _BRIGHTER
            return f"\033[1;38;5;{color}m{rendered}\033[0m"
        return f"\033[38;5;{color}m{rendered}\033[0m"


class NonSuccessOnly(logging.Filter):
    """Keep existing suppression of successful HTTP access messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name not in {"uvicorn.access", "httpx"}:
            return True
        match = _RESPONSE_STATUS.search(record.getMessage())
        return match is None or not match.group(1).startswith("2")


class ConsoleHandler(logging.StreamHandler):
    """Detect capabilities on the actual output stream in each process."""

    def __init__(self) -> None:
        super().__init__(sys.stderr)
        self.setFormatter(ModuleFormatter(self.stream))
        self.addFilter(NonSuccessOnly())


# Uvicorn reapplies this configuration in spawned reload workers.
LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "core.console_logging.ConsoleHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        name: {"handlers": [], "level": "INFO", "propagate": True}
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "httpx")
    },
}
