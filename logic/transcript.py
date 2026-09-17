"""Non-blocking, readable HTML game transcript persistence."""

import asyncio
import logging
from datetime import datetime
from html import escape
from pathlib import Path
import re

from core.schemas import ChanceEventResult, RoundResolution

LOGGER = logging.getLogger(__name__)


class GameTranscript:
    """Own one session's collision-safe HTML transcript."""

    def __init__(self, log_dir: Path = Path(".logged_games")) -> None:
        """Own one session's transcript under the given log directory."""
        self.log_dir = log_dir
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.path: Path | None = None
        self._finalized = False
        self._io_lock = asyncio.Lock()

    async def start(
        self, title: str, initial_state: str, opening_scenario: str = "", private_guidance: str = ""
    ) -> None:
        """Archive the host scenario and private guidance before the opening state."""
        safe_title = re.sub(r"[^\w\s-]", "", title).strip().replace(" ", "_")
        stem = f"{datetime.now():%Y-%m-%d}-{safe_title or 'session'}"
        self.path = self.log_dir / f"{stem}.html"
        suffix = 2
        while self.path.exists():
            self.path = self.log_dir / f"{stem}-{suffix}.html"
            suffix += 1
        scenario_html = (
            f'<h2>Original scenario prompt</h2>\n<p class="state">{escape(opening_scenario)}</p>\n'
            if opening_scenario
            else ""
        )
        guidance_html = (
            "<h2>Private DM guidance</h2>\n" f'<p class="state">{escape(private_guidance)}</p>\n'
            if private_guidance
            else ""
        )
        document = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>{escape(title)} — Anyworld</title><style>
*{{box-sizing:border-box}}
:root{{color-scheme:dark;--background:#0b1212;--panel:#141f20;--text:#e0e9e5;
--muted:#9aaca8;--border:rgb(216 224 231 / 10%);--accent:#7bc9ab;--action-accent:#ddb780}}
body{{max-width:60rem;margin:2rem auto;padding:0 1rem;background:var(--background);
color:var(--text);font:15px/1.6 "Segoe UI",system-ui,sans-serif;overflow-wrap:anywhere;
background-image:radial-gradient(ellipse at top,#162724,var(--background) 65%)}}
header,article{{margin:1.5rem 0;padding:clamp(1rem,3vw,2rem);border:1px solid var(--border);
border-radius:1rem;background:var(--panel);box-shadow:0 12px 40px rgb(0 0 0 / 15%)}}
h1{{margin:0 0 1.5rem;color:#a3e6c9;font-size:clamp(1.4rem,4vw,2rem)}}
h2{{color:var(--accent);font-size:1.05rem;margin:1.5rem 0 .75rem}}
article>h2{{margin-top:0;padding-bottom:.35rem;border-bottom:1px solid var(--action-accent);
color:var(--action-accent)}}
h3{{color:var(--muted);font-size:.85rem;letter-spacing:.04em}}
dt{{font-weight:600}}dd{{margin:0 0 .8rem;white-space:pre-wrap}}
.state{{white-space:pre-wrap;padding:1rem 1.15rem;border-left:3px solid var(--accent);
border-radius:.65rem;background:rgb(27 44 40 / 65%);color:var(--text)}}
.dice-rolls{{padding:.6rem 1rem;background:#1c2b2b;border:1px solid var(--border);
border-radius:.65rem;margin:.8rem 0}}
footer{{color:var(--muted);text-align:center;padding:1rem}}
@media(max-width:700px){{body{{margin:1rem auto;padding:0 .65rem}}}}
dl dt:nth-of-type(8n+1){{color:#79c0ff}}dl dt:nth-of-type(8n+2){{color:#ffa657}}
dl dt:nth-of-type(8n+3){{color:#56d364}}dl dt:nth-of-type(8n+4){{color:#ff7b72}}
dl dt:nth-of-type(8n+5){{color:#d2a8ff}}dl dt:nth-of-type(8n+6){{color:#f2cc60}}
dl dt:nth-of-type(8n+7){{color:#a5d6ff}}dl dt:nth-of-type(8n){{color:#ff9bce}}
</style></head><body><header><h1>Anyworld - {escape(title)}</h1>
{scenario_html}{guidance_html}<h2>Opening scenario</h2>
<p class="state">{escape(initial_state)}</p></header><main>
"""
        await self._write(document)
        LOGGER.info("Transcript started")

    async def append_round(
        self,
        number: int,
        actions: dict[str, str],
        resolution: RoundResolution,
        dice_results: dict[str, int] | None = None,
        player_colors: dict[str, int] | None = None,
        hidden_dice_results: dict[str, int] | None = None,
        chance_events: list[ChanceEventResult] | None = None,
    ) -> None:
        """Append a resolved round's actions, dice and results."""
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
        private_section = self._render_dice_section(
            hidden_dice_results, title="Private checks from DM guidance"
        )
        event_section = ""
        if chance_events:
            event_items = "".join(
                f"<dt>{escape(result.event.source_rule)}</dt>"
                f"<dd>{escape(result.event.occurrence)}: "
                f"{result.event.chance_percent}% chance; roll {result.roll}/100; "
                f"{'triggered' if result.occurred else 'not triggered'}"
                " (conditional events apply only if the trigger occurs).</dd>"
                for result in chance_events
            )
            event_section = (
                '<div class="dice-rolls"><h3>Private percentage events</h3>'
                f"<dl>{event_items}</dl></div>"
            )
        section = f"""<article><h2>Round {number}</h2>
<h3>Player actions</h3><dl>{actions_html}</dl>
{dice_section}
{private_section}
{event_section}
<h3>Results</h3><dl>{results_html}</dl><h3>Resulting state</h3>
<p class="state">{escape(resolution.global_narrative)}</p></article>
"""
        await self._write(section)
        LOGGER.info("Transcript round appended round=%d", number)

    @staticmethod
    def _render_dice_section(dice_results: dict[str, int] | None, title: str = "Dice rolls") -> str:
        """Render labeled public or private checks, omitting empty sections."""
        if not dice_results:
            return ""

        from logic.dice import describe_roll

        roll_items = []
        for name, value in sorted(dice_results.items()):
            description = describe_roll(value)
            label = f"{name}: {value}/100 ({description})"
            roll_items.append(f"<dt>{escape(label)}</dt><dd></dd>")
        return (
            f'<div class="dice-rolls"><h3>{escape(title)}</h3>'
            f"<dl>{''.join(roll_items)}</dl></div>"
        )

    async def finalize(self, reason: str = "The host ended the game.") -> None:
        """Close the transcript document and mark it finalized."""
        if self.path is None or self._finalized:
            return
        ending = f"</main><footer><p>{escape(reason)}</p></footer></body></html>\n"
        await self._write(ending, final=True)
        LOGGER.info("Transcript finalized")

    async def _write(self, text: str, *, final: bool = False) -> None:
        """Serialize and append text, waiting out in-flight writes on cancel."""
        async with self._io_lock:
            if self._finalized:
                if final:
                    return
                raise RuntimeError("Transcript has already been finalized")
            # Cancelling to_thread does not stop its underlying write. Keep the lock
            # until that write finishes so finalization cannot overtake it.
            work = asyncio.create_task(asyncio.to_thread(self._append, text))
            try:
                await asyncio.shield(work)
            except asyncio.CancelledError:
                await work
                if final:
                    self._finalized = True
                raise
            if final:
                self._finalized = True

    def _append(self, text: str) -> None:
        """Append text to the transcript file synchronously."""
        if self.path is None:
            raise RuntimeError("Transcript must be started before writing rounds")
        with self.path.open("a", encoding="utf-8") as log_file:
            log_file.write(text)
