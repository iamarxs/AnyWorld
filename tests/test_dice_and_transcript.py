"""Dice and HTML transcript tests."""

import asyncio
from pathlib import Path

from core.schemas import RoundResolution
from logic import dice
from logic.transcript import GameTranscript


def test_roll_d100_includes_both_boundaries(monkeypatch) -> None:
    """Verify d100 roll boundaries and their descriptions."""
    monkeypatch.setattr(dice.secrets, "randbelow", lambda upper: 0)
    assert dice.roll_d100() == 0

    monkeypatch.setattr(dice.secrets, "randbelow", lambda upper: upper - 1)
    assert dice.roll_d100() == 100
    assert dice.describe_roll(0) == "catastrophic failure"
    assert dice.describe_roll(100) == "perfect success, extra benefits"


def test_html_transcript_escapes_content_and_finalizes(tmp_path: Path) -> None:
    """Verify HTML escaping and finalization of the transcript."""

    async def run() -> None:
        transcript = GameTranscript(tmp_path)
        await transcript.start("A <Quest>", "An & opening")
        await transcript.append_round(
            1,
            {"Alice": "Uses <fire>"},
            RoundResolution(
                global_narrative="After & beyond",
                player_resolutions={"Alice": "Alice succeeds."},
            ),
        )
        await transcript.finalize("Finished")

        assert transcript.path is not None
        content = transcript.path.read_text(encoding="utf-8")
        assert transcript.path.suffix == ".html"
        assert "A &lt;Quest&gt;" in content
        assert "Uses &lt;fire&gt;" in content
        assert content.endswith("</html>\n")

    asyncio.run(run())
