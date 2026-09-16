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
        await transcript.start(
            "A <Quest>", "An & opening", "A <cabin> & a river.\nFind a way home."
        )
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
        assert "A &lt;cabin&gt; &amp; a river.\nFind a way home." in content
        assert content.index("Opening scenario") < content.index("Opening state")
        assert content.endswith("</html>\n")

    asyncio.run(run())


def test_private_transcript_sections_escape_content_and_omit_empty_sections(tmp_path):
    """Archive hidden checks without interpreting host guidance or player names as HTML."""

    async def run():
        transcript = GameTranscript(tmp_path)
        await transcript.start("Quest", "Opening", "Scenario", "Secret <trap> & trigger")
        await transcript.append_round(
            1,
            {"<Alice>": "Wait"},
            RoundResolution(global_narrative="A breeze.", player_resolutions={"<Alice>": "Waits."}),
            {"Bob": 75},
            hidden_dice_results={"<Alice>": 12},
        )
        content = transcript.path.read_text(encoding="utf-8")
        assert "Secret &lt;trap&gt; &amp; trigger" in content
        assert content.index("Opening scenario") < content.index("Private DM guidance")
        assert content.index("Private DM guidance") < content.index("Opening state")
        assert "&lt;Alice&gt;: 12/100" in content
        assert "Bob: 75/100" in content
        assert content.count("Private checks from DM guidance") == 1
        empty = GameTranscript(tmp_path)
        await empty.start("No secrets", "Opening")
        assert "Private DM guidance" not in empty.path.read_text(encoding="utf-8")
        assert GameTranscript._render_dice_section({}) == ""

    asyncio.run(run())
