"""Outcome cleanup preserves prose and avoids redundant player labels."""

import pytest

from logic.presentation import name_resolution


@pytest.mark.parametrize(
    "name, text, expected",
    [
        ("Arxs", "Arxs: Arxs: arxs finds a journal.", "Arxs finds a journal."),
        ("Arxs", "[Arxs] Arxs: He finds a journal.", "Arxs finds a journal."),
        ("Arxs", "A journal sits on the table.", "A journal sits on the table."),
        ("Arxs", "arxs finds a journal.", "Arxs finds a journal."),
        ("Will", "The door will open.", "The door will open."),
        ("Arxs", "Arx stands beside Arxs.", "Arx stands beside Arxs."),
        ("Ann", "Joanna looks at an annex.", "Joanna looks at an annex."),
        ("A+B", "A+B: The character waits.", "A+B waits."),
        (r"A\1", "He waits.", r"A\1 waits."),
        ("Arxs", "Arxs says: stay here.", "Arxs says: stay here."),
        (
            "Arxs",
            "[Arxs] He opens the gate.\n\nBeyond it, a path leads into the forest.",
            "Arxs opens the gate.\n\nBeyond it, a path leads into the forest.",
        ),
    ],
)
def test_name_resolution_is_conservative_and_idempotent(name, text, expected):
    """Cleanup runs in both the manager and engine, so repetition must be safe."""
    result = name_resolution(name, text)
    assert result == expected
    assert name_resolution(name, result) == expected
