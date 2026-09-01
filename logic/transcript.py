"""Non-blocking, readable HTML game transcript persistence."""

import asyncio
from datetime import datetime
from html import escape
from pathlib import Path
import re

from core.schemas import RoundResolution


class GameTranscript:
    """Own one session's collision-safe HTML transcript."""

    def __init__(self, log_dir: Path = Path(".logged_games")) -> None:
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.path: Path | None = None
        self._finalized = False

    async def start(self, title: str, initial_state: str) -> None:
        safe_title = re.sub(r"[^\w\s-]", "", title).strip().replace(" ", "_")
        stem = f"{datetime.now():%Y-%m-%d}-{safe_title or 'session'}"
        self.path = self.log_dir / f"{stem}.html"
        suffix = 2
        while self.path.exists():
            self.path = self.log_dir / f"{stem}-{suffix}.html"
            suffix += 1
        document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{escape(title)} — Anyworld</title><style>
body{{max-width:60rem;margin:2rem auto;padding:0 1rem;background:#0b1015;color:#d8e0e7;
font:16px/1.6 system-ui,sans-serif}}article{{margin:1.5rem 0;padding:1rem;border:1px solid #33404c;
border-radius:.5rem;background:#141b22}}h1,h2,h3{{color:#83c3df}}dt{{font-weight:700;color:#b49acb}}
dd{{margin:0 0 .8rem;white-space:pre-wrap}}
.state{{white-space:pre-wrap;color:#b8c4ce}}
.dice-rolls{{padding:.6rem;background:#1c2a3a;border:1px solid #4a5c6e;
border-radius:.4rem;margin:.8rem 0}}
dl dt:nth-of-type(8n+1){{color:#79c0ff}}dl dt:nth-of-type(8n+2){{color:#ffa657}}
dl dt:nth-of-type(8n+3){{color:#56d364}}dl dt:nth-of-type(8n+4){{color:#ff7b72}}
dl dt:nth-of-type(8n+5){{color:#d2a8ff}}dl dt:nth-of-type(8n+6){{color:#f2cc60}}
dl dt:nth-of-type(8n+7){{color:#a5d6ff}}dl dt:nth-of-type(8n){{color:#ff9bce}}
</style></head><body><header><h1>Anyworld - {escape(title)}</h1><h2>Opening state</h2>
<p class="state">{escape(initial_state)}</p></header><main>
"""
        await asyncio.to_thread(self._append, document)

    async def append_round(
        self,
        number: int,
        previous_state: str,
        actions: dict[str, str],
        resolution: RoundResolution,
        dice_results: dict[str, int] | None = None,
        player_colors: dict[str, int] | None = None,
    ) -> None:
        player_colors = player_colors or {}

        # Actions and results retain join order, so nth-of-type colors the same player
        # consistently without changing the readable transcript markup.
        actions_html = "".join(
            f"<dt>{escape(name)}</dt><dd>{escape(action)}</dd>" for name, action in actions.items()
        )
        results_html = "".join(
            f"<dt>{escape(name)}</dt><dd>{escape(result)}</dd>"
            for name, result in resolution.player_resolutions.items()
        )
        dice_section = self._render_dice_section(dice_results)
        section = f"""<article><h2>Round {number}</h2><h3>Prior state</h3>
<p class="state">{escape(previous_state)}</p><h3>Player actions</h3><dl>{actions_html}</dl>
{dice_section}
<h3>Results</h3><dl>{results_html}</dl><h3>Resulting state</h3>
<p class="state">{escape(resolution.global_narrative)}</p></article>
"""
        await asyncio.to_thread(self._append, section)

    @staticmethod
    def _render_dice_section(dice_results: dict[str, int] | None) -> str:
        """Render public dice results, or nothing when no rolls occurred."""
        if not dice_results:
            return ""

        from logic.dice import describe_roll

        roll_items = []
        for name, value in sorted(dice_results.items()):
            description = describe_roll(value)
            label = f"{name}: {value}/100 ({description})"
            roll_items.append(f"<dt>{escape(label)}</dt><dd></dd>")
        return '<div class="dice-rolls"><h3>Dice rolls</h3>' f"<dl>{''.join(roll_items)}</dl></div>"

    async def finalize(self, reason: str = "The host ended the game.") -> None:
        if self.path is None or self._finalized:
            return
        self._finalized = True
        ending = f"</main><footer><p>{escape(reason)}</p></footer></body></html>\n"
        await asyncio.to_thread(self._append, ending)

    def _append(self, text: str) -> None:
        if self.path is None:
            raise RuntimeError("Transcript must be started before writing rounds")
        with self.path.open("a", encoding="utf-8") as log_file:
            log_file.write(text)
