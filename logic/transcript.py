"""Non-blocking, human-readable game transcript persistence."""

import asyncio
import re
from datetime import datetime
from pathlib import Path

from core.schemas import RoundResolution


class GameTranscript:
    """Own one session's collision-safe transcript file."""

    def __init__(self, log_dir: Path = Path(".logged_games")) -> None:
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.path: Path | None = None

    async def start(self, title: str, initial_state: str) -> None:
        safe_title = re.sub(r"[^\w\s-]", "", title).strip().replace(" ", "_")
        stem = f"{datetime.now():%Y-%m-%d}-{safe_title or 'session'}"
        self.path = self.log_dir / f"{stem}.txt"
        suffix = 2
        while self.path.exists():
            self.path = self.log_dir / f"{stem}-{suffix}.txt"
            suffix += 1
        await asyncio.to_thread(
            self._append,
            f"=== SCENARIO GENESIS ===\nTitle: {title}\nInitial State: {initial_state}",
        )

    async def append_round(
        self,
        number: int,
        previous_state: str,
        actions: dict[str, str],
        resolution: RoundResolution,
    ) -> None:
        lines = [
            f"Round: {number}",
            f"State: {previous_state}",
            "",
            "Player actions:",
        ]
        lines.extend(f"{name}: {action}" for name, action in actions.items())
        lines.extend(["", "Results:"])
        lines.extend(f"{name}: {result}" for name, result in resolution.player_resolutions.items())
        lines.extend(["", f"Resulting state: {resolution.global_narrative}"])
        round_text = "\n\n" + "\n".join(lines)
        await asyncio.to_thread(self._append, round_text)

    def _append(self, text: str) -> None:
        if self.path is None:
            raise RuntimeError("Transcript must be started before writing rounds")
        with self.path.open("a", encoding="utf-8") as log_file:
            log_file.write(text)
