"""Dice and HTML transcript tests."""

import asyncio
from pathlib import Path

import pytest

from core.schemas import RoundResolution, StructuredChanceRule
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
        assert content.index("Original scenario prompt") < content.index("Opening scenario")
        assert content.endswith("</html>\n")

    asyncio.run(run())


@pytest.mark.parametrize("cadence", ["per_round", "condition"])
@pytest.mark.parametrize("scope", ["shared", "per_player"])
def test_private_guidance_has_subtitles_and_readable_chance_settings(tmp_path, cadence, scope):
    async def run():
        rule = StructuredChanceRule(
            chance_percent=15,
            cadence=cadence,
            trigger="Enter <cave> & inspect" if cadence == "condition" else "",
            eligibility="Players with <tools>" if cadence == "condition" else "",
            effect="Bosco helps <players> & leaves.",
            scope=scope,
        )
        transcript = GameTranscript(tmp_path)
        await transcript.start(
            "Quest",
            "Opening",
            private_guidance="Find caches.\n\nA <dwarf> appears.",
            chance_rule=rule,
        )
        content = transcript.path.read_text(encoding="utf-8")
        assert content.index("Freeform guidance") < content.index("Chance event")
        assert "Find caches.\n\nA &lt;dwarf&gt; appears." in content
        assert "<dt>Chance</dt><dd>15%</dd>" in content
        timing = "Every round" if cadence == "per_round" else "When the trigger occurs"
        assert f"<dt>Timing</dt><dd>{timing}</dd>" in content
        expected_scope = (
            "Shared event for the party"
            if scope == "shared"
            else "Separate event for each eligible player"
        )
        assert f"<dt>Scope</dt><dd>{expected_scope}</dd>" in content
        assert "<dt>Effect</dt><dd>Bosco helps &lt;players&gt; &amp; leaves.</dd>" in content
        if cadence == "condition":
            assert "<dt>Trigger</dt><dd>Enter &lt;cave&gt; &amp; inspect</dd>" in content
            assert "<dt>Eligibility</dt><dd>Players with &lt;tools&gt;</dd>" in content
        else:
            assert "<dt>Trigger</dt>" not in content
            assert "<dt>Eligibility</dt><dd>No additional restrictions</dd>" in content
        chance_only = GameTranscript(tmp_path)
        await chance_only.start("Chance only", "Opening", chance_rule=rule)
        content = chance_only.path.read_text(encoding="utf-8")
        assert "Private DM guidance" in content and "Chance event" in content
        assert "Freeform guidance" not in content
        await transcript.finalize()
        await chance_only.finalize()

    asyncio.run(run())


def test_transcript_colors_follow_identity_across_absence_and_sorted_dice(tmp_path):
    """An omitted player or alphabetical dice order cannot shift another player's color."""

    async def run():
        transcript = GameTranscript(tmp_path)
        await transcript.start("Quest", "Opening")
        colors = {"Absent": 0, "<Zoe>": 10, "Amy": 4}
        await transcript.append_round(
            1,
            {"Amy": "Wait", "<Zoe>": "Look"},
            RoundResolution(
                global_narrative="A quiet room.",
                player_resolutions={"<Zoe>": "Looks around.", "Amy": "Waits."},
            ),
            {"Amy": 75, "<Zoe>": 40},
            player_colors=colors,
            hidden_dice_results={"Amy": 12},
        )
        content = transcript.path.read_text(encoding="utf-8")
        assert content.count('<dt class="player-color-2">&lt;Zoe&gt;</dt>') == 2
        assert '<dt class="player-color-2">&lt;Zoe&gt;: 40/100' in content
        assert content.count('<dt class="player-color-4">Amy</dt>') == 2
        assert '<dt class="player-color-4">Amy: 75/100' in content
        assert '<dt class="player-color-4">Amy: 12/100' in content
        assert "nth-of-type" not in content
        await transcript.finalize()

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
        assert content.index("Original scenario prompt") < content.index("Private DM guidance")
        assert content.index("Private DM guidance") < content.index("Opening scenario")
        assert "&lt;Alice&gt;: 12/100" in content
        assert "Bob: 75/100" in content
        assert content.count("Private checks from DM guidance") == 1
        empty = GameTranscript(tmp_path)
        await empty.start("No secrets", "Opening")
        assert "Private DM guidance" not in empty.path.read_text(encoding="utf-8")
        assert GameTranscript._render_dice_section({}) == ""

    asyncio.run(run())
